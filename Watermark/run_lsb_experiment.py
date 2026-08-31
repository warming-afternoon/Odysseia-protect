"""在真实 Seraphina 角色卡上运行 LSB 对比实验并生成图表。"""

from __future__ import annotations

import csv
import io
import json
import math
import random
import statistics
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFont

from Watermark.lsb_watermark import (
    LSBWatermarkError,
    _byte_index,
    _replace_pixels,
    _rgba,
    embed_lsb,
    extract_lsb,
    logical_bit_error_rate,
)
from Watermark.watermark import (
    VerificationError,
    canonical_json,
    decrypt_envelope,
    inject_watermark,
    parse_png,
    serialize_png,
    verify_watermark,
)


ROOT = Path(__file__).parent
SOURCE_PATH = ROOT / "default_Seraphina.png"
OUTPUT_DIR = ROOT / "lsb_experiment_results"
TEST_KEY = bytes(range(32))
KEY_ID = "lsb-experiment-v1"
CARD_ID = "st-default-seraphina-v3"
PLACEMENT_KEY = bytes.fromhex(
    "079070545f9476a08ad8278159c751e835929ac1d6836a84b9401ad017d26699"
)


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    try:
        return ImageFont.truetype(name, size)
    except OSError:
        return ImageFont.load_default()


def _png(image: Image.Image, *, optimize: bool = False) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=optimize)
    return buffer.getvalue()


def _open_rgba(data: bytes) -> Image.Image:
    with Image.open(io.BytesIO(data)) as image:
        return image.convert("RGBA")


def _strip_metadata(data: bytes) -> bytes:
    keep = {b"IHDR", b"IDAT", b"IEND"}
    return serialize_png(chunk for chunk in parse_png(data) if chunk.chunk_type in keep)


def _resave_png(data: bytes) -> bytes:
    return _png(_open_rgba(data), optimize=True)


def _webp_round_trip(data: bytes, *, lossless: bool, quality: int = 90) -> bytes:
    image = _open_rgba(data)
    buffer = io.BytesIO()
    image.save(buffer, format="WEBP", lossless=lossless, quality=quality)
    with Image.open(io.BytesIO(buffer.getvalue())) as restored:
        return _png(restored.convert("RGBA"))


def _jpeg_round_trip(data: bytes, *, quality: int) -> bytes:
    image = _open_rgba(data).convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality, subsampling=0)
    with Image.open(io.BytesIO(buffer.getvalue())) as restored:
        return _png(restored.convert("RGBA"))


def _resize_round_trip(data: bytes, *, scale: float) -> bytes:
    image = _open_rgba(data)
    smaller = image.resize(
        (round(image.width * scale), round(image.height * scale)),
        Image.Resampling.LANCZOS,
    )
    restored = smaller.resize(image.size, Image.Resampling.LANCZOS)
    return _png(restored)


def _crop_shift(data: bytes, *, pixels: int) -> bytes:
    image = _open_rgba(data)
    cropped = image.crop((pixels, pixels, image.width, image.height))
    restored = Image.new("RGBA", image.size, (0, 0, 0, 0))
    restored.paste(cropped, (0, 0))
    return _png(restored)


def _mutate_lsb(data: bytes, *, fraction: float | None) -> bytes:
    width, height, pixels = _rgba(data)
    capacity = width * height * 3
    if fraction is None:
        positions = range(capacity)
    else:
        positions = random.Random(20260828).sample(
            range(capacity), round(capacity * fraction)
        )
    for position in positions:
        index = _byte_index(position)
        if fraction is None:
            pixels[index] &= 0xFE
        else:
            pixels[index] ^= 1
    image = Image.frombytes("RGBA", (width, height), bytes(pixels))
    return _replace_pixels(data, _png(image))


def _quality(original: bytes, candidate: bytes) -> dict[str, float | int | None]:
    left = _open_rgba(original).convert("RGB").tobytes()
    right = _open_rgba(candidate).convert("RGB").tobytes()
    if len(left) != len(right):
        raise ValueError("质量比较要求图片尺寸相同。")
    squared_error = 0
    absolute_error = 0
    changed_channels = 0
    changed_pixels = 0
    for offset in range(0, len(left), 3):
        pixel_changed = False
        for channel in range(3):
            difference = abs(left[offset + channel] - right[offset + channel])
            squared_error += difference * difference
            absolute_error += difference
            if difference:
                changed_channels += 1
                pixel_changed = True
        changed_pixels += int(pixel_changed)
    mse = squared_error / len(left)
    return {
        "mse": mse,
        "mae": absolute_error / len(left),
        "psnr_db": None if mse == 0 else 10 * math.log10(255 * 255 / mse),
        "changed_channels": changed_channels,
        "changed_pixels": changed_pixels,
    }


def _benchmark(action: Callable[[], Any], rounds: int = 80) -> dict[str, float]:
    samples: list[float] = []
    for _ in range(5):
        action()
    for _ in range(rounds):
        started = time.perf_counter()
        action()
        samples.append((time.perf_counter() - started) * 1000)
    ordered = sorted(samples)
    return {
        "mean_ms": statistics.mean(samples),
        "p50_ms": ordered[int(rounds * 0.50)],
        "p95_ms": ordered[int(rounds * 0.95)],
        "max_ms": max(samples),
    }


def _structure_recovers(data: bytes) -> bool:
    try:
        result = verify_watermark(
            data,
            keys={KEY_ID: TEST_KEY},
            expected_card_id=CARD_ID,
        )
        return result["valid"] is True
    except (VerificationError, ValueError):
        return False


def _lsb_recovers(
    data: bytes,
    expected_envelope: bytes,
    *,
    key: bytes | None,
    repetition: int,
) -> bool:
    try:
        encoded = extract_lsb(data, key=key, repetition=repetition)
        if encoded != expected_envelope:
            return False
        payload = decrypt_envelope(json.loads(encoded), {KEY_ID: TEST_KEY})
        return payload.get("card_id") == CARD_ID
    except (LSBWatermarkError, VerificationError, ValueError, json.JSONDecodeError):
        return False


def _bar_chart(
    path: Path,
    title: str,
    labels: list[str],
    series: list[tuple[str, list[float], tuple[int, int, int]]],
    *,
    unit: str,
) -> None:
    width, height = 1600, 850
    margin_left, margin_right, margin_top, margin_bottom = 150, 70, 110, 170
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    draw.text((margin_left, 35), title, fill="#172033", font=_font(38, bold=True))
    plot_width = width - margin_left - margin_right
    plot_height = height - margin_top - margin_bottom
    maximum = max(value for _, values, _ in series for value in values) * 1.15 or 1
    for tick in range(6):
        value = maximum * tick / 5
        y = margin_top + plot_height - plot_height * tick / 5
        draw.line((margin_left, y, width - margin_right, y), fill="#d8dee9", width=2)
        draw.text((25, y - 12), f"{value:.1f}", fill="#344054", font=_font(22))
    draw.text((20, margin_top - 45), unit, fill="#344054", font=_font(22))

    group_width = plot_width / len(labels)
    bar_width = min(90, group_width / (len(series) + 1))
    for group, label in enumerate(labels):
        center = margin_left + group_width * (group + 0.5)
        for index, (name, values, color) in enumerate(series):
            value = values[group]
            x0 = center + (index - (len(series) - 1) / 2) * bar_width - bar_width * 0.4
            x1 = x0 + bar_width * 0.8
            y0 = margin_top + plot_height - value / maximum * plot_height
            draw.rectangle((x0, y0, x1, margin_top + plot_height), fill=color)
            draw.text(
                (x0, y0 - 30),
                f"{value:.1f}",
                fill="#172033",
                font=_font(18),
            )
        label_box = draw.textbbox((0, 0), label, font=_font(20))
        draw.text(
            (center - (label_box[2] - label_box[0]) / 2, margin_top + plot_height + 25),
            label,
            fill="#172033",
            font=_font(20),
        )
    legend_x = margin_left
    legend_y = height - 70
    for name, _, color in series:
        draw.rectangle((legend_x, legend_y, legend_x + 28, legend_y + 20), fill=color)
        draw.text((legend_x + 40, legend_y - 5), name, fill="#172033", font=_font(20))
        legend_x += 310
    image.save(path)


def _survival_chart(path: Path, attacks: list[str], matrix: dict[str, list[bool]]) -> None:
    methods = list(matrix)
    cell_width, cell_height = 165, 92
    left, top = 300, 145
    width = left + cell_width * len(attacks) + 50
    height = top + cell_height * len(methods) + 80
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    draw.text((40, 35), "Watermark recovery after transformations", fill="#172033", font=_font(36, bold=True))
    for column, attack in enumerate(attacks):
        x = left + column * cell_width + cell_width / 2
        draw.text((x, top - 25), attack, fill="#344054", font=_font(17), anchor="ms")
    for row, method in enumerate(methods):
        y = top + row * cell_height
        draw.text((35, y + cell_height / 2), method, fill="#172033", font=_font(22), anchor="lm")
        for column, survived in enumerate(matrix[method]):
            x = left + column * cell_width
            fill = "#d8f3dc" if survived else "#fde2e1"
            mark = "PASS" if survived else "FAIL"
            color = "#176b3a" if survived else "#a12727"
            draw.rectangle((x + 4, y + 4, x + cell_width - 4, y + cell_height - 4), fill=fill)
            draw.text(
                (x + cell_width / 2, y + cell_height / 2),
                mark,
                fill=color,
                font=_font(22, bold=True),
                anchor="mm",
            )
    image.save(path)


def _bandwidth_chart(path: Path, downloads_per_second: float) -> None:
    width, height = 1600, 900
    left, right, top, bottom = 150, 90, 120, 150
    plot_width, plot_height = width - left - right, height - top - bottom
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    draw.text((left, 35), "30,000 downloads concentrated in 20:00-00:00", fill="#172033", font=_font(36, bold=True))
    max_size, max_mbps = 60, 1050
    for tick in range(0, 61, 10):
        x = left + tick / max_size * plot_width
        draw.line((x, top, x, top + plot_height), fill="#e4e7ec", width=2)
        draw.text((x, top + plot_height + 25), str(tick), fill="#344054", font=_font(21), anchor="ma")
    for tick in range(0, 1001, 200):
        y = top + plot_height - tick / max_mbps * plot_height
        draw.line((left, y, left + plot_width, y), fill="#e4e7ec", width=2)
        draw.text((left - 25, y), str(tick), fill="#344054", font=_font(21), anchor="rm")
    draw.text((left + plot_width / 2, height - 55), "Average file size (MB)", fill="#172033", font=_font(24), anchor="mm")
    draw.text(
        (left, top - 48),
        "Required bandwidth (Mbps)",
        fill="#172033",
        font=_font(24),
        anchor="ls",
    )

    points = []
    for size in range(max_size + 1):
        mbps = size * downloads_per_second * 8
        x = left + size / max_size * plot_width
        y = top + plot_height - mbps / max_mbps * plot_height
        points.append((x, y))
    draw.line(points, fill="#2864dc", width=6)
    for mbps, label, color in [
        (200, "Initial safe budget 200 Mbps", "#d97706"),
        (250, "Initial link 250 Mbps", "#b42318"),
        (1000, "Maximum 1 Gbps", "#176b3a"),
    ]:
        y = top + plot_height - mbps / max_mbps * plot_height
        draw.line((left, y, left + plot_width, y), fill=color, width=4)
        draw.text((left + 20, y - 32), label, fill=color, font=_font(21, bold=True))
    image.save(path)


def _visual_comparison(path: Path, original: bytes, sequential: bytes, repeated: bytes) -> None:
    source_image = _open_rgba(original)
    sequential_image = _open_rgba(sequential)
    repeated_image = _open_rgba(repeated)
    difference = ImageChops.difference(source_image, repeated_image).convert("RGB")
    difference = difference.point(lambda value: min(255, value * 255))
    panels = [
        ("Original", source_image.convert("RGB")),
        ("Sequential LSB", sequential_image.convert("RGB")),
        ("Scattered LSB x3", repeated_image.convert("RGB")),
        ("Difference x255", difference),
    ]
    gap, header = 20, 70
    width = gap + len(panels) * (source_image.width + gap)
    height = header + source_image.height + gap
    canvas = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)
    for index, (label, panel) in enumerate(panels):
        x = gap + index * (source_image.width + gap)
        canvas.paste(panel, (x, header))
        draw.text((x + source_image.width / 2, 32), label, fill="#172033", font=_font(24, bold=True), anchor="mm")
    canvas.save(path)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    original = SOURCE_PATH.read_bytes()
    structural, _ = inject_watermark(
        original,
        key=TEST_KEY,
        key_id=KEY_ID,
        user_id="999999999999999999",
        card_id=CARD_ID,
        resource_id="lsb-experiment-resource",
        issued_at=1_787_846_400,
        delivery_id="lsb-experiment-delivery",
    )
    envelope = next(
        chunk.data for chunk in parse_png(structural) if chunk.chunk_type == b"trAc"
    )
    sequential = embed_lsb(original, envelope)
    repeated = embed_lsb(original, envelope, key=PLACEMENT_KEY, repetition=3)
    composite = embed_lsb(structural, envelope, key=PLACEMENT_KEY, repetition=3)

    variants = {
        "Structure JSON+trAc": structural,
        "Sequential LSB": sequential,
        "Scattered LSB x3": repeated,
        "Composite": composite,
    }
    transforms: list[tuple[str, Callable[[bytes], bytes]]] = [
        ("Untouched", lambda data: data),
        ("Strip metadata", _strip_metadata),
        ("PNG resave", _resave_png),
        ("WebP lossless", lambda data: _webp_round_trip(data, lossless=True)),
        ("0.1% LSB noise", lambda data: _mutate_lsb(data, fraction=0.001)),
        ("Clear all LSB", lambda data: _mutate_lsb(data, fraction=None)),
        ("JPEG q95", lambda data: _jpeg_round_trip(data, quality=95)),
        ("WebP q90", lambda data: _webp_round_trip(data, lossless=False, quality=90)),
        ("Resize 90%", lambda data: _resize_round_trip(data, scale=0.9)),
        ("Crop 5px", lambda data: _crop_shift(data, pixels=5)),
        (
            "Strip+clear",
            lambda data: _strip_metadata(_mutate_lsb(data, fraction=None)),
        ),
    ]

    matrix: dict[str, list[bool]] = {method: [] for method in variants}
    detailed_results: list[dict[str, Any]] = []
    for method, source in variants.items():
        for attack, transform in transforms:
            attacked = transform(source)
            structure_ok = _structure_recovers(attacked)
            sequential_ok = _lsb_recovers(
                attacked, envelope, key=None, repetition=1
            )
            repeated_ok = _lsb_recovers(
                attacked, envelope, key=PLACEMENT_KEY, repetition=3
            )
            if method == "Structure JSON+trAc":
                recovered = structure_ok
                ber = None
            elif method == "Sequential LSB":
                recovered = sequential_ok
                ber = logical_bit_error_rate(attacked, envelope)
            elif method == "Scattered LSB x3":
                recovered = repeated_ok
                ber = logical_bit_error_rate(
                    attacked, envelope, key=PLACEMENT_KEY, repetition=3
                )
            else:
                recovered = structure_ok or repeated_ok
                ber = logical_bit_error_rate(
                    attacked, envelope, key=PLACEMENT_KEY, repetition=3
                )
            matrix[method].append(recovered)
            detailed_results.append(
                {
                    "method": method,
                    "attack": attack,
                    "recovered": recovered,
                    "structure_recovered": structure_ok,
                    "sequential_lsb_recovered": sequential_ok,
                    "repeated_lsb_recovered": repeated_ok,
                    "logical_bit_error_rate": ber,
                    "output_size": len(attacked),
                }
            )

    quality = {method: _quality(original, data) for method, data in variants.items()}
    performance = {
        "Structure JSON+trAc": {
            "inject": _benchmark(
                lambda: inject_watermark(
                    original,
                    key=TEST_KEY,
                    key_id=KEY_ID,
                    user_id="999999999999999999",
                    card_id=CARD_ID,
                    resource_id="bench",
                    issued_at=1_787_846_400,
                )[0]
            ),
            "extract": _benchmark(lambda: _structure_recovers(structural)),
        },
        "Sequential LSB": {
            "inject": _benchmark(lambda: embed_lsb(original, envelope)),
            "extract": _benchmark(lambda: extract_lsb(sequential)),
        },
        "Scattered LSB x3": {
            "inject": _benchmark(
                lambda: embed_lsb(
                    original, envelope, key=PLACEMENT_KEY, repetition=3
                )
            ),
            "extract": _benchmark(
                lambda: extract_lsb(
                    repeated, key=PLACEMENT_KEY, repetition=3
                )
            ),
        },
        "Composite": {
            "inject": _benchmark(
                lambda: embed_lsb(
                    inject_watermark(
                        original,
                        key=TEST_KEY,
                        key_id=KEY_ID,
                        user_id="999999999999999999",
                        card_id=CARD_ID,
                        resource_id="bench",
                        issued_at=1_787_846_400,
                    )[0],
                    envelope,
                    key=PLACEMENT_KEY,
                    repetition=3,
                )
            ),
            "extract": _benchmark(
                lambda: (
                    _structure_recovers(composite),
                    extract_lsb(composite, key=PLACEMENT_KEY, repetition=3),
                )
            ),
        },
    }

    downloads_per_second = 30_000 / (4 * 60 * 60)
    traffic = {
        "downloads_per_day": 30_000,
        "peak_window": "20:00-00:00",
        "peak_hours": 4,
        "average_requests_per_second_in_window": downloads_per_second,
        "initial_bandwidth_mbps": 250,
        "initial_safe_budget_mbps": 200,
        "maximum_bandwidth_mbps": 1000,
        "max_average_file_mb_at_250_mbps": 250 / 8 / downloads_per_second,
        "safe_average_file_mb_at_200_mbps": 200 / 8 / downloads_per_second,
        "max_average_file_mb_at_1_gbps": 1000 / 8 / downloads_per_second,
        "sample_required_mbps": len(original)
        / 1_000_000
        * downloads_per_second
        * 8,
    }

    (OUTPUT_DIR / "lsb_sequential.png").write_bytes(sequential)
    (OUTPUT_DIR / "lsb_scattered_r3.png").write_bytes(repeated)
    (OUTPUT_DIR / "triple_layer.png").write_bytes(composite)
    (OUTPUT_DIR / "triple_layer_jpeg95.png").write_bytes(
        _jpeg_round_trip(composite, quality=95)
    )
    _visual_comparison(
        OUTPUT_DIR / "visual_comparison.png", original, sequential, repeated
    )
    _survival_chart(
        OUTPUT_DIR / "survival_matrix.png",
        [name for name, _ in transforms],
        matrix,
    )
    _bar_chart(
        OUTPUT_DIR / "quality_psnr.png",
        "Visual distortion: higher PSNR is better",
        list(variants),
        [
            (
                "PSNR",
                [
                    quality[name]["psnr_db"] or 100.0
                    for name in variants
                ],
                (40, 100, 220),
            )
        ],
        unit="dB (100 = pixel-identical)",
    )
    _bar_chart(
        OUTPUT_DIR / "performance.png",
        "Watermark processing latency on Apple M1",
        list(variants),
        [
            (
                "Inject mean",
                [performance[name]["inject"]["mean_ms"] for name in variants],
                (40, 100, 220),
            ),
            (
                "Extract mean",
                [performance[name]["extract"]["mean_ms"] for name in variants],
                (23, 107, 58),
            ),
        ],
        unit="milliseconds",
    )
    _bandwidth_chart(OUTPUT_DIR / "bandwidth_capacity.png", downloads_per_second)

    report = {
        "sample": {
            "path": str(SOURCE_PATH),
            "size": len(original),
            "width": _open_rgba(original).width,
            "height": _open_rgba(original).height,
            "envelope_bytes": len(envelope),
        },
        "quality": quality,
        "performance": performance,
        "survival": detailed_results,
        "traffic": traffic,
    }
    (OUTPUT_DIR / "results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (OUTPUT_DIR / "survival.csv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=detailed_results[0].keys())
        writer.writeheader()
        writer.writerows(detailed_results)

    quality_rows = []
    performance_rows = []
    for name in variants:
        psnr = quality[name]["psnr_db"]
        quality_rows.append(
            f"| {name} | {'像素完全一致' if psnr is None else f'{psnr:.2f} dB'} | "
            f"{quality[name]['changed_pixels']} | {len(variants[name])} bytes |"
        )
        performance_rows.append(
            f"| {name} | {performance[name]['inject']['mean_ms']:.2f} ms | "
            f"{performance[name]['inject']['p95_ms']:.2f} ms | "
            f"{performance[name]['extract']['mean_ms']:.2f} ms |"
        )
    report_markdown = f"""# LSB 像素水印对比实验

实验日期：2026-08-28  
真实样本：`default_Seraphina.png`（400×600 RGBA，{len(original)} bytes）  
加密信封：{len(envelope)} bytes  
实验依赖：Pillow（仅位于 `watermark-experiment` 可选依赖）

## 当前结论

- 简单 LSB 对肉眼观感几乎没有影响，但只能抵抗 Metadata 清理和保持像素值的无损重保存。
- 顺序 LSB 遇到 0.1% 的稀疏最低位扰动即校验失败；密钥散布加三重复多数表决可通过该测试。
- 两种 LSB 都无法抵抗 JPEG、有损 WebP、缩放、裁剪、统一清零最低位或换图。
- `JSON + trAc + 散布 LSB×3` 的复合版本能覆盖更多非恶意处理路径，但主动执行“清 Metadata + 清 LSB”仍可完全移除。
- 角色卡的核心资产是 Prompt/世界书。图片 LSB 只能作为附加线索，不能取代 JSON 中的自包含加密凭证。

## 实验方案

1. `Structure JSON+trAc`：现有双层结构水印，像素不变。
2. `Sequential LSB`：把完整 AES-GCM 信封顺序写入 RGB 最低位。
3. `Scattered LSB×3`：通过密钥确定散布位置，每个逻辑位重复三次并多数表决。
4. `Composite`：结构水印与散布 LSB×3 同时存在。

LSB 只修改 RGB，Alpha 通道保持不变。载荷包含 Magic、协议版本、长度和 CRC32；恢复后还必须通过 AES-GCM 与 Card ID 校验。

## 视觉差异

![原图、LSB副本与255倍差异图](./visual_comparison.png)

| 方案 | PSNR | 变化像素 | 输出大小 |
|---|---:|---:|---:|
{chr(10).join(quality_rows)}

![PSNR对比](./quality_psnr.png)

`Scattered LSB×3` 改变 {quality['Scattered LSB x3']['changed_pixels']} 个像素，但每个通道最多只变化 1，PSNR 仍为 {quality['Scattered LSB x3']['psnr_db']:.2f} dB。差异需要放大255倍才容易观察。

## 破坏存活矩阵

![破坏测试存活矩阵](./survival_matrix.png)

需要注意：Metadata 被完全清理后，原本存放 Prompt 的 `chara/ccv3` 也会消失，文件已经不再是完整可导入的角色卡。此时 LSB 的“存活”只代表仍可从图片样本提取身份凭证。

## 性能

Apple M1、Python 3.12.9，真实600 KB样本，每项预热5次后运行80轮：

| 方案 | 注入平均 | 注入P95 | 提取平均 |
|---|---:|---:|---:|
{chr(10).join(performance_rows)}

![处理耗时](./performance.png)

复合方案平均注入约 {performance['Composite']['inject']['mean_ms']:.2f} ms，即当前单核约 {1000 / performance['Composite']['inject']['mean_ms']:.1f} 张/秒。像素PNG解码和重新编码是主要成本，AES-GCM与结构Chunk成本很小。

## 每日3万次、集中20:00–00:00的容量模型

这里按3万次全部均匀落在4小时内计算，平均为 {downloads_per_second:.3f} 次/秒：

- 当前600 KB样本需要约 {traffic['sample_required_mbps']:.2f} Mbps。
- 初始250 Mbps理论上支持平均约 {traffic['max_average_file_mb_at_250_mbps']:.1f} MB的资源。
- 按只使用80%带宽的200 Mbps安全预算，平均资源应不超过 {traffic['safe_average_file_mb_at_200_mbps']:.1f} MB。
- 带宽增长到1 Gbps后，理论平均资源上限约 {traffic['max_average_file_mb_at_1_gbps']:.1f} MB。

![带宽与资源大小关系](./bandwidth_capacity.png)

该模型只是4小时均匀基线。如果实际下载集中在其中几十分钟，应继续采集每分钟请求峰值，而不能只使用4小时平均值。

## 可复现命令

```bash
uv run --extra watermark-experiment python -m Watermark.run_lsb_experiment
uv run --extra watermark-experiment --extra test pytest -q tests/test_lsb_watermark.py tests/test_watermark.py
```

原始机器数据见 `results.json`，完整破坏矩阵见 `survival.csv`。
"""
    (OUTPUT_DIR / "REPORT.md").write_text(report_markdown, encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
