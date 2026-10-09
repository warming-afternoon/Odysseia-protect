# 技术架构


```
src/
├── cogs/                    # Discord 命令层（表现层）
│   ├── upload_cog.py       # /上传 命令
│   ├── download_cog.py     # /下载 命令
│   ├── manage_cog.py       # /管理 命令
│   └── info_cog.py         # /使用手册 命令
├── services/               # 业务逻辑层
│   ├── upload_service.py   # 上传业务逻辑
│   ├── download_service.py # 下载业务逻辑
│   ├── management_service.py # 管理业务逻辑
│   └── reaction_wall_service.py # 反应墙逻辑
├── database/               # 数据访问层
│   └── repositories/      # 仓库模式封装数据库操作
├── qo/                     # 创建与更新参数，一类一文件
├── dto/                    # 返回数据与共享数据基类，一类一文件
├── models/                 # SQLAlchemy ORM 模型，一类一文件
│   ├── base.py             # 共用声明式基类
│   ├── thread.py           # Thread
│   ├── resource.py         # Resource
│   ├── user.py             # User
│   ├── wishlist_item.py    # WishlistItem
│   ├── author_collaborator.py # AuthorCollaborator
│   └── trace_verification_job.py # TraceVerificationJob
├── enums/                  # 枚举（SourceStatus、UploadMode）
├── ui/                     # UI 组件层
│   ├── upload_ui.py       # 上传相关的 Modal 和 View
│   ├── download_ui.py     # 下载相关的 View
│   └── management_ui.py   # 管理相关的 View
└── utils/                  # 工具层
    ├── discord_utils.py   # Discord 相关工具函数
    └── formatting.py      # 格式化工具
```
