# 开源发布准备与进度 / Release readiness

## 本轮范围

仅完善开源 `storehelper` CLI 和 `storehelper-mcp`，未改动企业版前后端或 MVP。
目标版本：**CLI 0.8.1 → MCP 0.1.0 → MCP Registry**。当前是发布候选代码，不代表已经发布。

本轮使用独立 Git worktree，两个仓库分支均为 `codex/opensource-release-hardening`：

- CLI 工作目录：`opensource/storehelper-release`
- MCP 工作目录：`opensource/storehelper-mcp-release`

原 `opensource/storehelper`、`opensource/storehelper-mcp` 的 `main` 和未提交文件保持不变。
当前改动尚未提交、推送、合并或打标签；请在上述 worktree 中查看改动，避免误看原目录。

## 已完成

- [x] 保留八个应用市场适配器与既有发布/恢复/状态查询命令。
- [x] CLI 增加可选 `STOREHELPER_PROJECT_ROOT`；不配置时保留原有全局记录行为。
- [x] MCP 强制使用指定项目的 `.storehelper/runs`，不读取/恢复其他项目或全局记录。
- [x] 校验配置、安装包、发布说明、小米图标、记录内安装包路径及软链接目标。
- [x] 只读查询不创建项目状态目录；新增记录保持目录/文件私有权限。
- [x] MCP 流式限制 stdout/stderr 总量；异常退出、超时、取消均有回归测试。
- [x] 使用隔离 Python 模式启动 CLI，阻止项目中的同名模块冒充已安装代码。
- [x] wheel/sdist 采用白名单；通过实际打包测试检查本地目录、缓存、配置和密钥文件混入。
- [x] 发布版本校验、Ruff、mypy、覆盖率门槛、归档检查、独立 wheel 安装验证。
- [x] TestPyPI 独立演练要求唯一 `.devN` 版本，避免占用正式版本号。
- [x] tag 发布复用同一份构建产物：TestPyPI → PyPI → GitHub Release，附 SHA256SUMS。
- [x] MCP Registry `server.json`、PyPI 所有权标识、版本/命名校验和受保护手动工作流。
- [x] 完善 README、安全说明、客户端配置示例、变更日志和发布操作文档。

## 本地验证证据

执行环境为 macOS / Python 3.12.6；没有使用真实应用市场密钥或执行真实发布。

| 检查 | CLI | MCP |
| --- | --- | --- |
| 完整 pytest | 964 passed | 147 passed |
| 覆盖率 | 91.13% | 92.25% |
| Ruff 检查与格式 | 通过 | 通过 |
| mypy | 84 个源文件通过 | 7 个源文件通过 |
| sdist → wheel 构建 | 通过 | 通过 |
| Twine strict / 归档边界 / SHA256SUMS | 通过 | 通过 |
| 独立虚拟环境安装实际 wheel | 通过 | 通过 |
| 安装后离开源码目录执行 | 版本、帮助、初始化、配置校验通过 | stdio 握手、8 工具发现、只读默认值、真实 CLI 桥接通过 |

MCP manifest 同时通过本地语义校验与官方 `2025-12-11` JSON Schema 校验。
工作流 YAML 已解析检查，但 GitHub Actions/OIDC/发布环境审批尚未在线执行。
CI 配置覆盖 Python 3.11–3.14；这些版本矩阵不等同于已在本机全部运行。

## 仍需维护者完成的外部步骤

- [ ] 确认 GitHub 组织/仓库公开可访问及发布权限；确认 PyPI 项目名称可用或归己方所有。
- [ ] 配置 PyPI/TestPyPI Trusted Publishers（工作流文件名、仓库和 environment 必须完全一致）。
- [ ] 配置 GitHub `testpypi`、`pypi`、`mcp-registry` 的审批与允许分支/tag。
- [ ] 审阅改动、提交 PR、合并并等待远端 CI 通过。
- [ ] 先完成 CLI 的 TestPyPI 演练，再授权正式 CLI 0.8.1 发布。
- [ ] CLI 可从公共 PyPI 安装后，再演练并发布 MCP 0.1.0。
- [ ] 在实际使用的 MCP 客户端验收；记录客户端版本与测试结果。
- [ ] 单独审批 MCP Registry 发布，并检查公开记录。
- [ ] 使用专门测试应用完成各市场真实端到端验收；本地 mock 测试不能替代这一项。

操作步骤见本仓库 [RELEASING.md](RELEASING.md)，以及 MCP 仓库的
`docs/RELEASING.md`、`docs/RELEASE_SETUP.md`。
Homebrew、Flutter 和 IDE 插件不是此次发布前加固的交付范围。

## 安全与兼容边界

普通 CLI 的运行记录不会自动迁移到项目目录。MCP 使用的 CLI 必须至少为 0.8.1，不能为了
绕过依赖安装错误而降低版本约束。项目范围约束不是操作系统沙箱；需要信任本地用户、解释器、
已安装依赖和项目目录的所有者。应用市场已经接收的操作不会因 MCP 超时/取消而自动撤销，
出现不确定结果时先查询状态，不要直接重试提交。默认关闭 mutation，不在提示词或配置示例中放密钥。
