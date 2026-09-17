# OCR 离线规则原型

用于验证无交互界面、无 Git/模型调用的规则复用，不是生产 Reviewer 或完整 Delegation。

## 已验证范围

- 上游固定提交：`4e59c7e815bde045158b549cdc2a7f58a32f6ba0`。
- 原样复用 `rules.LoadDefault` 与 `delegate.GroupRules`；本目录只包含新增入口与测试，不复制上游规则资产。
- 输入是冻结的相对路径列表；仅使用内置系统规则，不读用户/项目规则配置，也不读取路径对应的源码。
- 不执行文件筛选、Diff 生成、内容语言识别、模型调用或审查。README.md 同样得到默认规则，不能把规则返回视为审查完成。
- Windows LPAC、Win32k 禁用、禁网、单进程、只读副本下已通过；macOS 尚未实测。

## 构建

准备上述固定版本的官方源码和 Go 1.25.5。可在上游源码的独立临时副本中，
将本目录的 main.go 和 main_test.go 放入 `cmd/coding-agent-offline-rules/`，然后执行：

```text
go test ./cmd/coding-agent-offline-rules
go build -trimpath -o ocr-offline-rules.exe ./cmd/coding-agent-offline-rules
```

本次实测为了避免拉取完整 CLI 依赖，采用更小的临时构建目录：

1. 原样复制上游 `internal/config/rules`、`internal/delegate`、`internal/gitcmd`、`internal/pathutil` 四个目录。
2. 放入本目录的两个 Go 文件，保留上游 LICENSE 和适用 NOTICE。
3. 使用以下 go.mod；go.sum 中该依赖的两条记录从固定上游 go.sum 原样复制。
4. 将 GOPATH/GOCACHE 指向临时目录，设置 GOTOOLCHAIN=local、GOENV=off、CGO_ENABLED=0；
   用独立 Go 工具链运行上面两条命令。只执行新入口的测试，不声称执行上游全部测试。

```go
module github.com/alibaba/open-code-review

go 1.25.5

require github.com/bmatcuk/doublestar/v4 v4.10.0
```

这不是新增项目 Go 依赖或发布包；构建物留在临时目录。固定来源字符串不是密码学验证，
构建者必须使用对应官方源码；未来产品接入需要额外记录资产/二进制指纹。

## 输入与输出

将以下内容以 UTF-8 保存为 input.json，然后执行 `ocr-offline-rules.exe input.json`：

```json
{"schema_version":"ocr.offline-paths/v1","paths":["sample.py","new.py","README.md"]}
```

输出 schema 为 `coding-agent.ocr-rules-probe/v1`，包含上游提交、能力范围及 files/rule 分组。
输入最大 256 KiB、1–1000 个不重复相对路径；非法路径、控制目录、未知字段/版本及尾随 JSON
返回非零退出码且不输出规则结果。路径在此仅为规则选择键；生产端仍须独立校验任务授权范围。

## 复验记录与限制

真实 Windows 探测复用 `WindowsReadOnlyProcess(runtime_roots=(binary_directory,))`，
显式使用默认 `verification=False`。原型可执行文件和 JSON 输入经现有后端映射进副本；
没有提供 Git runtime，没有开放 scratch 写入或子进程，也没有注入模型凭据。

本机临时目录 `coding-agent-ocr-headless-qmj8tvnw/native-final/records/probe.jsonl`
记录了调用前请求及实际结果。合法输入退出 0；路径穿越、控制目录、未知版本、超大输入均退出 1。
Python 规则与官方 v1.12.4 普通进程输出逐字一致；文件指纹未变。
Go 单元测试通过；这些结果只证明规则入口能力，不证明审查质量或 P10 完成。

下一步产品化需要决定是否采用该裁剪入口，补资产许可/版本管理、任务上下文与自定义规则合并、
调用留痕及门禁映射。内置规则不替代项目显式规范。
