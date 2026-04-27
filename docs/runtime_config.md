# runtime_config.md

## 1. 文档目的

本文档定义一次运行的配置对象、参数优先级与校验规则。它只描述运行时输入、阈值、路径、工具链版本和派生规则，不描述算法实现。

## 2. 适用范围

- 适用于单次批处理任务的启动参数与会话参数
- 适用于数据入口、标定、匹配、辐射、重建与富集各阶段的公共配置
- 适用于写入运行清单的可审计配置快照

## 3. 配置优先级

运行时配置必须遵循以下优先级：

1. [docs/dataset_profile.md](dataset_profile.md) 中定义的不可变物理事实
2. 用户显式传入的运行参数、会话参数或命令行参数
3. 阶段级默认值
4. 运行时派生值

派生值只能从前面的层级计算得到，不能反向覆盖物理事实。

## 4. 运行配置对象

### 4.1 必要字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `workspace_root` | string | 是 | 工作区根目录 |
| `dataset_id` | string | 是 | 数据集标识，必须与 dataset_profile 一致 |
| `dataset_profile_ref` | string | 是 | 指向 dataset_profile 的版本号、摘要或路径引用 |
| `input_dataset_root` | string | 是 | 业务数据根目录 |
| `calibration_dataset_root` | string | 是 | 标定数据根目录 |
| `output_root` | string | 是 | 本次运行输出根目录 |
| `run_id` | string | 是 | 唯一运行标识，生成规则见 file_formats.md |
| `pipeline_version` | string | 是 | 流程版本号 |
| `stage_selection` | array[string] | 是 | 本次启用的阶段列表 |
| `quality_thresholds` | object | 是 | 阶段阈值集合 |
| `environment_parameters` | object | 是 | 热辐射层所需环境参数 |
| `toolchain_versions` | object | 是 | Python / Metashape / TWMM 等工具链版本 |

### 4.2 工具链版本对象

`toolchain_versions` 至少应记录以下内容：

- `python_version`
- `metashape_version`
- `twmm_source_ref`
- `exiftool_version`
- `pipeline_schema_version`

## 5. 校验规则

- 数据集路径必须存在，且其中影像数量与 dataset_profile 的边界一致。
- 输出目录必须可写，且 `run_id` 对应的目录不能与现有任务冲突。
- `stage_selection` 只能包含 architecture.md 中定义的阶段名。
- `quality_thresholds` 中的所有数值阈值都必须标明单位或无量纲属性。
- `environment_parameters` 必须使用明确单位，不能只写一个无单位数字。
- 如果任何必要字段缺失，配置层必须拒绝进入后续阶段，而不是默默使用隐式默认值。

## 6. 与其他文档的关系

- [docs/architecture.md](architecture.md) 定义配置层在流水线中的位置。
- [docs/file_formats.md](file_formats.md) 定义运行清单、持久化配置快照和各阶段产物的结构。
- [docs/dataset_profile.md](dataset_profile.md) 定义不能被配置覆盖的物理事实。
- [docs/radiometry_model.md](radiometry_model.md) 定义环境参数如何进入温度矩阵生成。

## 7. 非目标

- 不是命令行解析器实现说明。
- 不是参数持久化格式的唯一约束，持久化契约仍由 file_formats.md 定义。
- 不是把物理事实写成可被任意覆盖的默认值集合。