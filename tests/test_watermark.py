"""验证宏注释、独立恢复、内容绑定及旧版兼容。"""

import copy
import json
from pathlib import Path

import pytest

from src.traceability.watermark import (
    MACRO_FIELDS,
    MACRO_RE,
    MAX_MACRO_CANDIDATES,
    MAX_MACRO_ENCODED_CHARS,
    TRACE_CHUNK_TYPE,
    PNGChunk,
    VerificationError,
    WatermarkError,
    _b64encode,
    build_text_chunk,
    canonical_json,
    card_content_sha256,
    card_documents,
    clean_card_document,
    create_envelope,
    encode_card_json,
    inject_json_watermark,
    inject_watermark,
    parse_png,
    serialize_png,
    verify_watermark,
)

KEY = bytes(range(32))
KEYS = {"test": KEY}
OPTIONS = dict(
    key=KEY,
    key_id="test",
    user_id="42",
    card_id="card",
    resource_id="resource",
    issued_at=100,
    delivery_id="delivery",
    allow_empty_fields=True,
)
CARD_PATH = Path(__file__).parents[1] / "Watermark" / "default_Seraphina.png"


@pytest.fixture
def original():
    return CARD_PATH.read_bytes()


@pytest.fixture
def marked(original):
    return inject_watermark(original, **OPTIONS)[0]


def rewrite(data, transform):
    chunks = parse_png(data)
    docs = {i: (k, d) for i, k, d in card_documents(chunks)}
    return serialize_png(
        (
            build_text_chunk(docs[i][0], encode_card_json(transform(docs[i][1])))
            if i in docs
            else c
        )
        for i, c in enumerate(chunks)
    )


def test_real_card_roundtrip_mirrors_and_clean_content(original, marked):
    result = verify_watermark(marked, keys=KEYS, strict_layers=True)
    assert result["sources"] == ["macro:ccv3", "macro:chara", "png:trAc"]
    assert result["content_status"] == "not_checked"
    assert [c.data for c in parse_png(original) if c.chunk_type == b"IDAT"] == [
        c.data for c in parse_png(marked) if c.chunk_type == b"IDAT"
    ]
    before = card_documents(parse_png(original))
    after = card_documents(parse_png(marked))
    fields = set()
    for (_, _, old), (_, _, new) in zip(before, after):
        assert clean_card_document(new) == clean_card_document(old)
        assert "odysseia_trace" not in new["data"]["extensions"]
        selected = [f for f in MACRO_FIELDS if MACRO_RE.search(new["data"][f])]
        assert len(selected) == 1
        fields.update(selected)
        assert new[selected[0]] == new["data"][selected[0]]
        assert new["description"] == old["description"]
    assert len(fields) == 1


def test_random_label_shared_across_png_documents(original):
    """每次签发生成新标签，同一次签发的所有镜像保持一致。"""
    labels = []
    for _ in range(2):
        output, _ = inject_watermark(original, **OPTIONS)
        matches = [
            match
            for _, _, document in card_documents(parse_png(output))
            for scope in (document, document["data"])
            for field in MACRO_FIELDS
            for match in MACRO_RE.finditer(scope.get(field, ""))
        ]
        assert len(matches) == 4
        assert len({match.group("label") for match in matches}) == 1
        labels.append(matches[0].group("label"))
        assert all("odysseia_trace" not in match.group(0) for match in matches)
    assert labels[0] != labels[1]


def test_json_reissue_changes_label_preserves_digest():
    """重新签发移除旧注释，更新身份和标签并保持内容摘要。"""
    source = canonical_json({"scenario": "", "personality": "Calm"})
    first, first_payload = inject_json_watermark(source, **OPTIONS)
    second, second_payload = inject_json_watermark(
        first, **(OPTIONS | {"user_id": "43"})
    )
    first_match = MACRO_RE.search(json.loads(first)["scenario"])
    second_match = MACRO_RE.search(json.loads(second)["scenario"])
    assert first_match.group("label") != second_match.group("label")
    assert len(MACRO_RE.findall(json.loads(second)["scenario"])) == 1
    assert "content_sha256" not in first_payload
    assert "content_sha256" not in second_payload
    result = verify_watermark(second, keys=KEYS, strict_layers=True)
    assert result["payload"]["uid"] == "43"
    assert result["content_status"] == "not_checked"


def test_ordinary_comments_and_old_brand_macro_are_preserved():
    """普通注释和未上线旧品牌注释不被清理或作为有效凭证读取。"""
    envelope = create_envelope({"v": 1, "uid": "old"}, KEY, "test")
    old_macro = "{{//odysseia_trace:" + _b64encode(canonical_json(envelope)) + "}}"
    note = "{{//0123456789abcdef:" + _b64encode(canonical_json({"note": "hello"})) + "}}"
    document = {"personality": "{{//a normal comment}}" + note, "scenario": old_macro}
    assert clean_card_document(document) == document
    with pytest.raises(VerificationError, match="没有找到"):
        verify_watermark(canonical_json(document), keys=KEYS)
    document["mes_example"] = ""
    output, _ = inject_json_watermark(canonical_json(document), **OPTIONS)
    assert clean_card_document(json.loads(output)) == document
    result = verify_watermark(output, keys=KEYS, strict_layers=True)
    assert result["content_status"] == "not_checked"


@pytest.mark.parametrize("action", ["verify", "clean", "issue"])
@pytest.mark.parametrize("limit", ["count", "length"])
def test_macro_limits_apply_to_all_entrypoints(action, limit):
    """核验、摘要清理和签发对单文档候选使用相同上限。"""
    if limit == "count":
        text = "{{//0123456789abcdef:bad}}" * (MAX_MACRO_CANDIDATES + 1)
        message = "128"
    else:
        text = "{{//0123456789abcdef:" + "A" * (MAX_MACRO_ENCODED_CHARS + 1) + "}}"
        message = "32 KiB"
    document = {"scenario": text, "personality": ""}
    with pytest.raises(WatermarkError, match=message):
        if action == "verify":
            verify_watermark(canonical_json(document), keys=KEYS)
        elif action == "clean":
            clean_card_document(document)
        else:
            inject_json_watermark(canonical_json(document), **OPTIONS)


def test_candidate_count_includes_mirrors_and_allows_boundary():
    """候选上限包含顶层镜像且允许恰好达到限制的文档。"""
    candidate = "{{//0123456789abcdef:bad}}"
    document = {
        "scenario": candidate * 64,
        "data": {"scenario": candidate * 64},
    }
    assert clean_card_document(document) == document
    document["data"]["scenario"] += candidate
    with pytest.raises(WatermarkError, match="128"):
        card_content_sha256(document)


def test_macro_length_boundary_and_malformed_content_are_preserved():
    """允许长度边界且不删除无法解码的候选注释。"""
    candidate = "{{//0123456789abcdef:" + "!" * MAX_MACRO_ENCODED_CHARS + "}}"
    original = {"scenario": candidate, "personality": ""}
    assert clean_card_document(original) == original
    marked, _ = inject_json_watermark(canonical_json(original), **OPTIONS)
    result = verify_watermark(marked, keys=KEYS)
    assert result["content_status"] == "not_checked"
    assert result["layer_status"] == "degraded"
    assert clean_card_document(json.loads(marked)) == original


def test_issuance_counts_new_macro_against_candidate_limit():
    """签发不能在已满候选上限的文档中继续添加凭证。"""
    document = {
        "scenario": "{{//0123456789abcdef:bad}}" * MAX_MACRO_CANDIDATES,
        "personality": "",
    }
    assert clean_card_document(document) == document
    with pytest.raises(WatermarkError, match="128"):
        inject_json_watermark(canonical_json(document), **OPTIONS)


def test_unknown_internal_version_is_reported_without_external_version():
    """版本从信封内部读取，未知版本不会被误认为认证成功。"""
    envelope = create_envelope({"v": 1, "uid": "test"}, KEY, "test")
    envelope["v"] = 999
    macro = "{{//0123456789abcdef:" + _b64encode(canonical_json(envelope)) + "}}"
    document = {"scenario": macro}
    with pytest.raises(VerificationError, match="不支持的溯源协议版本"):
        verify_watermark(canonical_json(document), keys=KEYS)
    assert clean_card_document(document) == {"scenario": ""}


def test_legacy_extension_reissue_preserves_other_extensions():
    """正式服旧扩展继续可核验，重新签发时只清理本系统扩展。"""
    envelope = create_envelope({"v": 1, "uid": "old"}, KEY, "test")
    original = {"scenario": "", "extensions": {"third_party": {"setting": True}}}
    legacy = copy.deepcopy(original)
    legacy["extensions"]["odysseia_trace"] = envelope
    assert verify_watermark(canonical_json(legacy), keys=KEYS)["payload"]["uid"] == "old"
    assert card_content_sha256(legacy) == card_content_sha256(original)
    output, _ = inject_json_watermark(canonical_json(legacy), **OPTIONS)
    assert clean_card_document(json.loads(output)) == original
    result = verify_watermark(output, keys=KEYS, strict_layers=True)
    assert result["sources"] == ["macro:standalone"]
    assert result["payload"]["uid"] == "42"
    assert result["content_status"] == "not_checked"


def test_reinjection_replaces(marked, original):
    options = OPTIONS | {"user_id": "43", "delivery_id": "replacement"}
    output, _ = inject_watermark(marked, **options)
    result = verify_watermark(output, keys=KEYS, strict_layers=True)
    assert result["payload"]["uid"] == "43"
    for _, _, d in card_documents(parse_png(output)):
        assert sum(len(MACRO_RE.findall(d["data"][f])) for f in MACRO_FIELDS) == 1


def test_exported_json_and_json_ingestion(marked):
    for _, _, document in card_documents(parse_png(marked)):
        raw = canonical_json(document)
        assert verify_watermark(raw, keys=KEYS, strict_layers=True)["credential_valid"]
        output, _ = inject_json_watermark(raw, **OPTIONS)
        assert verify_watermark(output, keys=KEYS)["credential_valid"]


def test_empty_priority_and_nonempty_preservation():
    document = {
        "data": {
            "description": "unchanged",
            "mes_example": "{{pick::a::b}}",
            "personality": "text",
            "scenario": "",
        }
    }
    output, payload = inject_json_watermark(canonical_json(document), **OPTIONS)
    result = json.loads(output)
    assert payload["macro_field"] == "scenario"
    assert clean_card_document(result) == document
    document["data"]["scenario"] = "some story"
    for _ in range(5):
        output, payload = inject_json_watermark(canonical_json(document), **OPTIONS)
        assert payload["macro_field"] in {"personality", "scenario"}
        assert clean_card_document(json.loads(output)) == document


@pytest.mark.parametrize(
    "value", [None, {}, [], 1, "{{unclosed", "}}", "/command", "<% script %>"]
)
def test_unsafe_fields_are_not_coerced(value):
    document = {
        "data": {field: value for field in MACRO_FIELDS},
        "description": "do not use",
    }
    with pytest.raises(WatermarkError, match="安全"):
        inject_json_watermark(canonical_json(document), **OPTIONS)


def test_png_skips_macro_safely(original):
    def unsafe(d):
        for scope in (d, d["data"]):
            for f in MACRO_FIELDS:
                scope[f] = "{{pick::a::b}}"
        return d

    raw = rewrite(original, unsafe)
    output, payload = inject_watermark(raw, **OPTIONS)
    assert payload["macro_field"] is None
    assert verify_watermark(output, keys=KEYS, strict_layers=True)["sources"] == [
        "png:trAc"
    ]


@pytest.mark.parametrize(
    "broken",
    [
        b"{",
        b"[]",
        b'{"v":1,"kid":"test","nonce":5,"ciphertext":"x"}',
        b'{"v":1,"kid":"unknown"}',
    ],
)
def test_bad_trac_does_not_block_macros(marked, broken):
    output = serialize_png(
        PNGChunk(TRACE_CHUNK_TYPE, broken) if c.chunk_type == TRACE_CHUNK_TYPE else c
        for c in parse_png(marked)
    )
    result = verify_watermark(output, keys=KEYS)
    assert result["credential_valid"]
    assert result["layer_status"] == "degraded"
    assert result["missing_sources"] == ["png:trAc"]
    with pytest.raises(VerificationError):
        verify_watermark(output, keys=KEYS, strict_layers=True)


def test_malformed_card_does_not_block_other_layers(marked):
    output = serialize_png(
        (
            build_text_chunk("chara", b"garbage")
            if (c.chunk_type == b"tEXt" and c.data.startswith(b"chara\0"))
            else c
        )
        for c in parse_png(marked)
    )
    assert verify_watermark(output, keys=KEYS)["valid"]


def test_authenticated_conflicts_rejected(marked):
    payload = verify_watermark(marked, keys=KEYS)["payload"] | {"uid": "other"}
    envelope = create_envelope(payload, KEY, "test")
    output = serialize_png(
        (
            PNGChunk(TRACE_CHUNK_TYPE, canonical_json(envelope))
            if c.chunk_type == TRACE_CHUNK_TYPE
            else c
        )
        for c in parse_png(marked)
    )
    with pytest.raises(VerificationError, match="冲突"):
        verify_watermark(output, keys=KEYS)


def test_wrong_key_and_trusted_context(marked):
    with pytest.raises(VerificationError, match="认证失败"):
        verify_watermark(marked, keys={"test": b"x" * 32})
    for kwargs in (
        {"expected_card_id": "wrong"},
        {"expected_resource_id": "wrong"},
        {"expected_content_sha256": "wrong"},
    ):
        with pytest.raises(VerificationError, match="不匹配"):
            verify_watermark(marked, keys=KEYS, **kwargs)


def test_modified_content_keeps_credential_without_claiming_content_match(marked):
    def modify(d):
        d["data"]["description"] += "changed"
        return d

    result = verify_watermark(rewrite(marked, modify), keys=KEYS)
    assert result["credential_valid"]
    assert result["content_status"] == "not_checked"
    assert result["content_match"] is None


@pytest.fixture
def export_card():
    """构造包含本次实际导出差异及世界书正文的角色卡。"""
    return {
        "create_date": "2026-11-11T18:37:00.047Z",
        "talkativeness": 0.5,
        "data": {
            "name": "test",
            "description": "Original description",
            "personality": "Calm",
            "scenario": "",
            "mes_example": "",
            "extensions": {"talkativeness": 0.5, "third_party": True},
            "character_book": {
                "extensions": {},
                "entries": [{"content": "Original lore"}],
            },
        },
    }


def test_normal_export_changes_preserve_credential(export_card):
    """正常导出不影响签发凭证认证，签发时也不改写原文。"""
    before = copy.deepcopy(export_card)
    marked, payload = inject_json_watermark(canonical_json(export_card), **OPTIONS)
    assert "content_digest_version" not in payload
    assert "content_sha256" not in payload
    assert clean_card_document(json.loads(marked)) == before
    exported = json.loads(marked)
    exported["create_date"] = "2026-11-12T13:46:19.836Z"
    exported["talkativeness"] = "0.5"
    exported["data"]["extensions"]["talkativeness"] = "0.5"
    del exported["data"]["character_book"]["extensions"]
    result = verify_watermark(canonical_json(exported), keys=KEYS, strict_layers=True)
    assert result["content_status"] == "not_checked"
    assert card_content_sha256(before) == card_content_sha256(exported)
    assert export_card == before


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("data", "name"), "changed"),
        (("data", "description"), "Changed description"),
        (("data", "personality"), "Angry"),
        (("data", "character_book", "entries", 0, "content"), "Changed lore"),
        (("talkativeness",), "0.6"),
        (("data", "extensions", "talkativeness"), "0.6"),
        (("data", "extensions", "third_party"), False),
        (("data", "character_book", "extensions"), {"custom": True}),
        (("talkativeness",), True),
        (("talkativeness",), "0.50000000000000000001"),
    ],
)
def test_content_edits_do_not_prevent_credential_authentication(export_card, path, value):
    """正文、世界书和配置被修改后仍可认证保留下来的凭证。"""
    marked, _ = inject_json_watermark(canonical_json(export_card), **OPTIONS)
    edited = json.loads(marked)
    target = edited
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    result = verify_watermark(canonical_json(edited), keys=KEYS)
    assert result["credential_valid"]
    assert result["content_status"] == "not_checked"


@pytest.mark.parametrize("digest_version", [1, 2])
def test_legacy_digest_does_not_affect_credential_authentication(export_card, digest_version):
    """旧凭证中的摘要不参与默认核验，显式可信摘要参数仍可校验。"""
    digest = card_content_sha256(export_card, version=digest_version)
    payload = {
        "v": 1,
        "uid": "old",
        "content_sha256": [digest],
    }
    if digest_version == 2:
        payload["content_digest_version"] = 2
    envelope = create_envelope(payload, KEY, "test")
    result = verify_watermark(
        canonical_json(export_card), keys=KEYS, extra_envelopes=[("pixel:lsb", envelope)]
    )
    assert result["content_status"] == "not_checked"
    assert verify_watermark(
        canonical_json(export_card), keys=KEYS,
        extra_envelopes=[("pixel:lsb", envelope)], expected_content_sha256=digest,
    )["credential_valid"]
    export_card["create_date"] = "new export date"
    result = verify_watermark(
        canonical_json(export_card), keys=KEYS, extra_envelopes=[("pixel:lsb", envelope)]
    )
    assert result["content_status"] == "not_checked"


def test_unused_digest_version_does_not_affect_authentication(export_card):
    """历史内容摘要字段不参与签发凭证核验。"""
    payload = {"v": 1, "uid": "test", "content_digest_version": 999}
    envelope = create_envelope(payload, KEY, "test")
    result = verify_watermark(
        canonical_json(export_card), keys=KEYS,
        extra_envelopes=[("pixel:lsb", envelope)],
    )
    assert result["credential_valid"]
    assert result["content_status"] == "not_checked"


def test_legacy_envelope_still_readable():
    payload = {"v": 1, "uid": "legacy", "card_id": "old"}
    document = {
        "data": {
            "extensions": {"odysseia_trace": create_envelope(payload, KEY, "test")}
        }
    }
    result = verify_watermark(canonical_json(document), keys=KEYS)
    assert result["sources"] == ["legacy:standalone"]
    assert result["content_binding"] == "not_checked"


def test_external_lsb_can_recover_without_other_layers(original):
    _, payload = inject_watermark(
        original, **OPTIONS, expected_extra_sources=["pixel:lsb"]
    )
    result = verify_watermark(
        original,
        keys=KEYS,
        extra_envelopes=[("pixel:lsb", create_envelope(payload, KEY, "test"))],
    )
    assert result["sources"] == ["pixel:lsb"]
    assert result["layer_status"] == "degraded"


def test_explicit_strict_profile_skips_empty_fields(original):
    options = OPTIONS | {"allow_empty_fields": False}
    output, payload = inject_watermark(original, **options)
    assert payload["macro_status"] == "skipped_unsafe_or_empty"
    assert verify_watermark(output, keys=KEYS, strict_layers=True)["sources"] == [
        "png:trAc"
    ]


def test_cli_json_verification(tmp_path, capsys):
    from src.traceability.watermark import main

    output, _ = inject_json_watermark(
        canonical_json({"personality": "ordinary text"}), **OPTIONS
    )
    path = tmp_path / "card.json"
    path.write_bytes(output)
    assert (
        main(
            [
                "verify",
                str(path),
                "--key",
                _b64encode(KEY),
                "--expected-resource-id",
                "resource",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["content_status"] == "not_checked"


def test_conflicting_mirror_is_not_modified():
    document = {"data": {"personality": "nested"}, "personality": "different"}
    with pytest.raises(WatermarkError, match="安全"):
        inject_json_watermark(canonical_json(document), **OPTIONS)


def test_surviving_valid_layer_with_different_nonce(marked):
    payload = verify_watermark(marked, keys=KEYS)["payload"]
    envelope = create_envelope(payload, KEY, "test")
    result = verify_watermark(
        marked, keys=KEYS, extra_envelopes=[("pixel:lsb", envelope)]
    )
    assert result["credential_valid"]


def test_malformed_macro_recovers_png(marked):
    def corrupt(d):
        for scope in (d, d["data"]):
            for field in MACRO_FIELDS:
                if isinstance(scope.get(field), str):
                    scope[field] = MACRO_RE.sub("{{//0123456789abcdef:!bad!}}", scope[field])
        return d

    result = verify_watermark(rewrite(marked, corrupt), keys=KEYS)
    assert result["sources"] == ["png:trAc"]
    assert any(layer["status"] == "malformed" for layer in result["layer_results"])
    assert result["content_status"] == "not_checked"


@pytest.mark.parametrize("suffix", [" ", "   ", "\n", "\t", "\r\n", " \n\t", "\u3000"])
def test_macro_precedes_trailing_whitespace(suffix):
    text = (
        "<START>\n{{char}}: ordinary example".replace("{{char}}", "Character") + suffix
    )
    document = {"data": {"mes_example": text}, "mes_example": text}
    output, payload = inject_json_watermark(
        canonical_json(document), **(OPTIONS | {"allow_empty_fields": False})
    )
    marked = json.loads(output)
    assert payload["macro_field"] == "mes_example"
    assert clean_card_document(marked) == document
    assert card_content_sha256(marked) == card_content_sha256(document)
    for scope in (marked, marked["data"]):
        value = scope["mes_example"]
        assert value.startswith("<START>")
        assert value.endswith("}}" + suffix)
        assert MACRO_RE.sub("", value.strip()) == text.strip()
    assert verify_watermark(output, keys=KEYS)["content_status"] == "not_checked"


def test_default_prefers_empty_fields(original):
    options = {
        key: value for key, value in OPTIONS.items() if key != "allow_empty_fields"
    }
    output, payload = inject_watermark(original, **options)
    assert payload["allow_empty_fields"] is True
    assert payload["macro_status"] == "inserted"
    assert (
        verify_watermark(output, keys=KEYS, strict_layers=True)["layer_status"]
        == "complete"
    )


@pytest.mark.parametrize("removed", [{"chara"}, {"chara", "ccv3"}])
def test_signed_manifest_detects_removed_documents(marked, removed):
    from src.traceability.watermark import parse_text_chunk

    output = serialize_png(
        c
        for c in parse_png(marked)
        if not ((text := parse_text_chunk(c)) and text[0] in removed)
    )
    result = verify_watermark(output, keys=KEYS)
    assert result["missing_sources"] == sorted("json:" + name for name in removed)
    with pytest.raises(VerificationError, match="缺少"):
        verify_watermark(output, keys=KEYS, strict_layers=True)


def test_optional_unavailable_layer_does_not_fail_strict(marked):
    result = verify_watermark(
        marked,
        keys=KEYS,
        strict_layers=True,
        extra_layer_results=[{"source": "pixel:lsb", "status": "unavailable"}],
    )
    assert result["layer_status"] == "complete"
    assert result["layer_results"][0]["status"] == "unavailable"


def test_signed_unavailable_layer_fails_strict(original):
    output, _ = inject_watermark(
        original, **OPTIONS, expected_extra_sources=["pixel:lsb"]
    )
    with pytest.raises(VerificationError, match="缺少"):
        verify_watermark(
            output,
            keys=KEYS,
            strict_layers=True,
            extra_layer_results=[{"source": "pixel:lsb", "status": "unavailable"}],
        )


def test_present_malformed_extra_layer_remains_visible(marked):
    result = verify_watermark(marked, keys=KEYS, extra_envelopes=[("pixel:lsb", None)])
    assert result["layer_status"] == "degraded"
