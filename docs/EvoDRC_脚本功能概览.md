# EvoDRC 脚本功能概览

> 审计基线：当前工作区 `0a9b4c7`。本表依据实际函数、导入、调用点和写入路径整理。`agent/` 是 EvoDRC 自维护的可替换 Agent；`DAC26_DRC_Benchmark/` 是 v1 Benchmark 子模块，表中仅列 EvoDRC 实际调用的冻结基础设施。Python 标准库（如 `json`、`os`、`subprocess`、`re`、`xml.etree`）不属于仓库脚本；KLayout/`pya`、Claude Code CLI、Docker、`networkx` 是外部依赖。

## EvoDRC 自维护代码

| 路径 | 分类 | 主要入口/函数 | 输入 | 输出 | 核心作用 | 主要依赖 |
| -- | -- | -- | -- | -- | -- | -- |
| `agent/agent.py` | 运行入口 | `main()` | 格式化 prompt、CLI 参数、环境变量 | 修复脚本/检测 JSON、状态与 token 标记 | 选择 backend；块修复转入迭代控制器，其余任务单次调用模型 | `agent_backend.*`、`agent.src.agent_entry` |
| `agent/prompt_format.py` | 运行入口 | `render()`、`main()` | case-info JSON、prompt 模板 | 含 `EVODRC_CASE_INFO` 的 Markdown prompt | Benchmark 级提示词格式化；不要与 `agent/src/prompt_format.py` 混淆 | Python 标准库 |
| `agent/evodrc.conf` | 配置与环境 | `KEY=VALUE` | 运行参数 | 由 `iter/conf.py` 解析的配置 | 定义消融、迭代上限、并发、知识更新和冲突竞赛开关 | `agent/src/iter/conf.py` |
| `agent/skill.md` | Agent 提示词与 Skill | 固定规则指南 | ASAP7 规则与修复约束 | `skill_official.md` 注入快照 | 给 Repair Agent 的固定背景规则；不承担程序执行 | Markdown |
| `agent/prompts/*.json` | Agent 提示词与 Skill | repair/detection 模板 | case-info 占位符 | 格式化 prompt | Benchmark 外层任务模板 | `agent/prompt_format.py` |
| `agent/src/agent_entry.py` | 运行入口 | `parse_case_info_from_prompt()`、`run_src_block_repair()` | prompt、输出路径、模型与工作目录 | `(status, error)` | 从 prompt 提取 case-info，构造 `CaseContext`，启动迭代修复 | `iter/controller.py`、`types.py` |
| `agent/src/pipeline.py` | 运行入口 | `run_pipeline()`、`stage_emit()` | `CaseContext` | patched layout | 旧的单轮 S1–S11 流程；当前块级迭代入口未调用它 | parse/model/clip/split/dispatch/mount 模块 |
| `agent/src/parse_case.py` | 版图解析 | `stage_parse_case()` | 格式化 prompt | `CaseInfo` | 旧流程的 case-info 解析阶段 | Python 标准库 |
| `agent/src/model.py` | 版图解析 | `stage_model()`、`_build_geometry_model()`、`_parse_drc_violations()` | `BlockX.py`、`.drc.json` | `GeometryModel`、`Violation[]`；带 anchor 的脚本文本 | 解析 polygon/cell/instance/DRC，并插入 `polygon_id`、`instance_id` 注释锚点 | `types.py`、`subcell_protection.py` |
| `agent/src/types.py` | 版图解析 | `Polygon`、`Violation`、`Leaf`、`Patch`、`CaseContext` | Python 数据 | 共享数据对象 | 定义各阶段传递的数据结构 | `dataclasses` backport |
| `agent/src/calibrate.py` | 版图解析 | `stage_calibrate()` | 几何模型、违规 | `BlockStats`、校准参数 | 从当前块统计尺度与规则阈值 | `types.py` |
| `agent/src/clips.py` | 违规分解 | `stage_build_clips()`、`stage_merge_clips()` | 违规与几何模型 | `Clip[]` | 构造并合并局部违规簇 | `dependency_graph.py`、`model.py` |
| `agent/src/dependency_graph.py` | 违规分解 | `Graph`、`build_polygon_graph()` | polygon/clip/违规 | 图结构 | 建立 polygon 依赖图 | Python 标准库 |
| `agent/src/predicates.py` | 违规分解 | 多个判定函数 | 违规子图 | 布尔判定 | 判断子图是否可独立修复 | `types.py` |
| `agent/src/hrd_split.py` | 违规分解 | `stage_hrd_split()`、`hrd_split_recursive()`、`build_union_leaf()` | merged clips、几何、规则 | `ctx.leaves`、crop 脚本 | 用图切分递归拆分违规簇，分配可编辑对象/实例并输出 leaves | `networkx`、`crop_body.py`、`layer_band.py` |
| `agent/src/_invariant.py` | 违规分解 | `unit_touch_failures()` | leaf、违规、可编辑对象 | 不变量失败列表 | 确认每个违规在 unit 内有可编辑触碰者 | `types.py` |
| `agent/src/_relocate.py` | 违规分解 | `relocate_pdn_owned()`、`relocate_c2_empty()` | leaves、所有权 | 重分配后的 leaves | 将违规迁移给真正拥有可修对象的 leaf | `model.py` |
| `agent/src/enforcer_p5e.py` | 冲突仲裁 | ownership pass | 跨 leaf 对象 | 唯一所有权 | 两遍处理共享 polygon/instance 所有权 | `types.py` |
| `agent/src/pdn_prepass.py` | 违规分解 | `stage_pdn_prepass()` | 电源网相关几何 | PDN proto-leaves/所有权 | 识别供电网络并单独分配修复区域 | `networkx`、`conn_check.py` |
| `agent/src/strap_compact.py` | 违规分解 | `stage_strap_compact()` | leaves、金属几何 | 压缩后的 strap 上下文 | 识别并压缩电源 strap 模式 | `types.py` |
| `agent/src/longstripe.py` | 违规分解 | stripe 分类函数 | polygon 与 leaf | 长条带分类 | 避免跨块长金属使 crop 失控 | Python 标准库 |
| `agent/src/layer_band.py` | 违规分解 | `layers_of_rule()`、`band_for_leaf()` | rule id / leaf | editable/background layer 集 | 将规则映射到层带 | Python 标准库 |
| `agent/src/subcell_protection.py` | 版图解析 | `detect_subcell_kind()`、`allowed_ops_for()` | cell 名称 | 类型与允许操作 | 区分标准单元与 via，保护冻结单元内部 | Python 标准库 |
| `agent/src/subcell_inclusion.py` | 版图解析 | inclusion helpers | leaf、cell 几何 | prompt 可见的子单元几何 | 选择应放入局部上下文的实例形状 | `types.py` |
| `agent/src/crop_body.py` | 局部修复 | crop render helpers | `Leaf`、`GeometryModel` | 可执行 KLayout crop 片段 | 渲染 Repair Agent 可检查的局部版图脚本 | `pya` 语法（输出侧） |
| `agent/src/scheduler.py` | 任务分发 | `stage_schedule()` | leaves | wave/调度信息 | 旧流程按互斥编辑区域分波 | `types.py` |
| `agent/src/dispatch.py` | 模型调用 | `dispatch_leaf()`、`stage_dispatch()` | leaf prompt | `LeafResult` | 旧流程逐 leaf 调 backend 并解析/验证 patch | `agent_backend`、`validator.py` |
| `agent/src/prompt_format.py` | Agent 提示词与 Skill | `build_leaf_prompt()` | leaf、规则卡、crop、三个 preview 上下文 | unit prompt 正文 | 构造局部修复提示、操作契约与工具指令 | `types.py`、context modules |
| `agent/src/patch_parser.py` | Patch解析与应用 | `parse_patch_from_file_text()`、`parse_patch_json()` | 纯 JSON 或 fenced JSON | `Patch` / `None` | 校验 envelope、匹配 `leaf_id`，接受 `ops`/`patch` 别名 | Python `json` |
| `agent/src/patch_apply.py` | Patch解析与应用 | `apply_patch_to_text()` | layout 文本、`Patch`、`Leaf` | 修改后的 layout 文本 | 按锚点执行 resize/move/delete/jog/add polygon/via/instance 操作 | Python `re` |
| `agent/src/validator.py` | 局部修复 | `validate()` | patch、leaf、context | `Verdict` | 旧 dispatch 流程的 provenance、网格、边界、连通性等静态检查 | `types.py` |
| `agent/src/mount.py` | Patch解析与应用 | `stage_mount()` | 已接受 leaf patches | `ctx.patched_layout_text` | 旧流程串行挂载 patch 并复验连通性 | `patch_apply.py`、`connectivity.py` |
| `agent/src/connectivity.py` | 连通性检查 | `is_connectivity_preserved()` | golden connectivity JSON、候选脚本、design type | `bool` | 动态加载 Benchmark `check_connectivity()` 并收敛成硬布尔值 | Benchmark evaluator |
| `agent/src/conn_check.py` | 连通性检查 | block/cell tracing helpers | layout 脚本 | pin/net/路径模型 | 为内部上下文与 PDN 分析解析连通结构 | `networkx` |
| `agent/src/conn_context.py` | 连通性检查 | `write_context()` | case、leaf | `ctx/conn/context_*.json` | 写出 `conn_preview` 所需的只读上下文 | `conn_check.py` |
| `agent/src/conn_impact_context.py` | 连通性检查 | `write_context()` | case、leaf | `ctx/connimpact/context_*.json` | 写出对象断开影响查询上下文 | `conn_check.py` |
| `agent/src/conn_preview.py` | 连通性检查 | `main()` | 候选 ops、conn context | JSON verdict | 对候选 patch 预演全块连通性 | `patch_apply.py`、Benchmark evaluator |
| `agent/src/conn_impact_preview.py` | 连通性检查 | `main()` | 对象 id、impact context | JSON 影响说明 | 查询删/移对象可能破坏的连接 | Python 标准库 |
| `agent/src/drc_context.py` | 全块DRC | `write_context()` | case、leaf | `ctx/drc/context_*.json` | 写出 `drc_preview` 所需局部上下文 | `types.py` |
| `agent/src/drc_preview.py` | 全块DRC | `main()` | 候选 ops、DRC context | JSON verdict | 应用候选操作并运行 crop DRC 预览 | `drc_check.py`、`patch_apply.py` |
| `agent/src/drc_check.py` | 全块DRC | `run_faithful_crop_drc()` | leaf、布局、deck | crop 前后 DRC 多重集 | 渲染局部 GDS 并调用 KLayout 规则 deck | KLayout、`drc_postprocess.py` |
| `agent/src/drc_oracle.py` | 全块DRC | oracle helpers | crop DRC 前后结果 | 接受/拒绝 | 旧流程可选的局部 DRC 增量门 | `drc_check.py` |
| `agent/src/drc_postprocess.py` | 全块DRC | `parse_lyrpt()`、`build_drc_json()`、`process_single_file()` | KLayout `.lyrpt`、layout 脚本 | `.drc.json` | 解析 XML 违规、换算 dbu、去重，并将点违规回填 polygon bbox | Python XML/JSON |
| `agent/src/tokens.py` | 模型调用 | `aggregate()` 等 | backend usage JSON | 汇总 token 统计 | 统一单次与多次模型调用计量 | Python 标准库 |
| `agent/src/logging_setup.py` | 配置与环境 | `setup_logger()` | 日志级别 | 结构化日志 | 提供流水线统一 logger/stage 字段 | Python `logging` |
| `agent/src/iter/controller.py` | 运行入口 | `run_iterative_block_repair()` | `CaseContext` | 最佳连通修复脚本、迭代树、状态 | 驱动最多 `MAX_ITERS` 轮的分解、修复、门控、组装、评测和演化 | 所有 `iter/*` 核心模块 |
| `agent/src/iter/conf.py` | 配置与环境 | `resolve()` | `evodrc.conf`、环境变量 | `RunConfig`、规范化环境变量 | 解析消融、并发、竞赛与迭代参数 | Python 标准库 |
| `agent/src/iter/paths.py` | 配置与环境 | `resolve_persist_root()`、`ensure_fresh_iter_dir()` | case/model/ablation | `data/<exp>/<case>/<model>/iterN` | 管理发布目录、命名清洗和文本限长 | Python 标准库 |
| `agent/src/iter/workdir.py` | 配置与环境 | `resolve_work_root()`、`sweep_unit()` | persist root、unit dir | 容器内 scratch / 清理后的发布目录 | 将内部临时文件与发布产物分离 | Python 标准库 |
| `agent/src/iter/decompose.py` | 违规分解 | `decompose_ctx()`、`leaves_json()` | 本轮 `BlockX.py`、`.drc.json` | `CaseContext`、`leaves.json` | 调 `stage_model` 到 `stage_schedule`，生成稳定 leaves | model/calibrate/clips/hrd_split |
| `agent/src/iter/union_layout.py` | 违规分解 | `detect_rail_ys()`、`compute_row_layout()`、`group_units()` | leaves、M1 rails、块边界 | unit 列表 | 同行单行 leaf 合并为 row union，多行/PDN 独立 | Python 标准库 |
| `agent/src/iter/plan.py` | 任务分发 | `plan_units()`、`plan_whole()` | leaves | `unions.json`、`host_leaf.*.json`、`host_union.*.json` | 建 repair units、隔离无可编辑触碰者的 unit、注册 union | `union_layout.py`、`_invariant.py` |
| `agent/src/iter/schedule.py` | 任务分发 | `run_leaves()` | repairable unit ids、公共参数 | `leaf/<UNIT>/...` | 以受限并发启动独立 `leaf_runner` 子进程 | Python `subprocess`、`throttle.py` |
| `agent/src/iter/leaf_runner.py` | 局部修复 | `main()`、`_run_unit()` | 本轮布局/DRC、unit、知识与工具路径 | `prompt.txt`、`patch.json`、`ctx/` | 重做分解校验 unit，生成上下文与 prompt，调用 Claude backend，规范化 patch | `agent_backend.claude`、prompt/context/parser |
| `agent/src/iter/prompt_exp3.py` | Agent 提示词与 Skill | `build_header()`、`build_shared_target_context()` | unit、知识路径、技术目录 | exp3 prompt 头与共享目标说明 | 让模型按路径读取规则/知识/crop，并支持 whole-design 模式 | `prompt_format.py` |
| `agent/src/iter/throttle.py` | 模型调用 | `CallGate`、`get_gate()` | 并发与冷却配置 | 调用时隙 | 统一限制 repair 和 knowledge 模型调用 | 文件锁/线程同步 |
| `agent/src/iter/gate.py` | 连通性检查 | `gate_leaf()` | `patch.json`、本轮脚本文本、golden connectivity | `<UNIT>.verdict` | 硬拒绝缺失/空/错 leaf/不可解析 patch、应用失败及连通性破坏 | `patch_parser.py`、`patch_apply.py`、`connectivity.py` |
| `agent/src/iter/cu_drc.py` | 冲突仲裁 | `extract_candidates()`、`run_pool()` | 多 unit 的共享目标候选 | `cu_verdicts.json`、winner pool | 对共享目标覆盖窗口做真实 DRC/连通性竞赛，选每个冲突分量至多一个 winner | `drc_check.py`、`connectivity.py` |
| `agent/src/iter/via_competition.py` | 冲突仲裁 | `run_competition()` | 同一 via cell 的多个候选 | winner 与分数 | 在隔离 cell 上运行 KLayout DRC，选择共享 via 定义编辑 | KLayout |
| `agent/src/iter/assemble.py` | 冲突仲裁 | `assemble()` | gated unit patches、原脚本文本、竞赛结果 | `repaired/BlockX.py`、`*.assembled.json` | 串行应用无冲突操作；普通 polygon 首到先得，共享 via 竞赛，instance 分歧全丢弃 | `patch_apply.py`、`via_competition.py` |
| `agent/src/iter/block_eval.py` | 全块DRC | `render_block_drc()`、`run_block_drc_nested()` | repaired `.py`、deck、golden conn、输入 DRC | GDS、LYRPT、DRC JSON、`block_result.json` | 改写 `layout.write`，两次调用 KLayout，后处理报告并做全块连通性/增量统计 | KLayout、`drc_postprocess.py`、`connectivity.py` |
| `agent/src/iter/evolve.py` | 知识演化 | `build_records()`、`update_layer()`、`update_all()` | gate/assembly/DRC 结果、历史知识 | `ledger_summary.json`、`knowledge_update.json`、layer-wise Skill | 记录测量经验；并行生成 Markov/Stateless 候选，再由 judge 选择并更新层知识 | `layerdb.py`、Claude backend |
| `agent/src/iter/layerdb.py` | 知识演化 | `ensure_branch()`、`append_records()`、`write_knowledge()`、`build_main_md()` | deck map、trial records、候选知识 | `db/layerdb/<LAYER>/`、`skill/<LAYER>.md`、`db/main.md` | 文件化的按层规则、追加历史、知识与索引数据库 | Python 标准库 |
| `agent/src/iter/seed.py` | 知识演化 | `ensure_seed()` | `knowledge/cla` 或 `cold_start` | 初始 `db/` 与 `skill/` | 按消融模式建立层知识库并校验 seed | `layerdb.py` |
| `agent/src/iter/inject.py` | 知识演化 | `stage()` | 当前 `db/`、`skill/` | `_inject/`、本轮 `input/` 快照、manifest | 将规则索引和 layer-wise Skill 复制为只读 prompt 输入 | `layerdb.py` |
| `agent/src/iter/crop_history.py` | 知识演化 | `snapshot()` | `leaf/<UNIT>/ctx` | `crop_history/iterN/...` | 归档每轮 unit 上下文 | Python `shutil` |
| `agent/src/iter/textutil.py` | 配置与环境 | `ascii_sanitize()` | 任意文本 | ASCII 文本 | 清洗持久化记录中的自由文本 | Python 标准库 |
| `Dockerfile.evodrc` | 配置与环境 | Docker build | Benchmark repair 镜像 | EvoDRC repair 镜像 | 为 Python 3.6 镜像补装 `networkx==2.5.1` 与 `dataclasses` | Docker、pip |

## DAC26 Benchmark 冻结/可信评测代码

| 路径 | 分类 | 主要入口/函数 | 输入 | 输出 | 核心作用 | 主要依赖 |
| -- | -- | -- | -- | -- | -- | -- |
| `DAC26_DRC_Benchmark/src/evaluate_claude.sh` | Benchmark评测 | 顶层批处理循环 | 模型/案例矩阵、测试集、认证挂载 | result/score/log/temp | 建隔离容器，运行 agent-only，断网/杀残留进程，再注入可信 evaluator 执行 score-only | Docker、`lib_helpers.sh` |
| `DAC26_DRC_Benchmark/src/run_pipeline_claude.sh` | Benchmark评测 | agent/score/full phases | `info.json` | repaired 脚本、GDS/DRC/score JSON | 格式化 prompt、调用 `agent.py`，可信阶段独立渲染、DRC、sanity、连通性与评分 | KLayout、evaluator scripts |
| `DAC26_DRC_Benchmark/src/lib_helpers.sh` | Benchmark评测 | `disconnect_container_network()`、`kill_leftover_processes()`、`write_trusted_agent_meta()` | 容器、agent stderr | 可信元数据与清理后的运行状态 | 在宿主侧隔离模型阶段与评测阶段，并汇总 token | Docker、可信二进制 |
| `DAC26_DRC_Benchmark/src/build_case_info.py` | Benchmark评测 | `main()` | CLI case 参数 | `info.json` | 生成宿主侧案例描述 | Python 标准库 |
| `DAC26_DRC_Benchmark/evaluator/postprocess_info_json.py` | Benchmark评测 | `main()` | host `info.json` 与容器路径 | case-info JSON | 将输入路径映射成容器内路径 | Python 标准库 |
| `DAC26_DRC_Benchmark/src/agent_backend/claude.py` | 模型调用 | `call_agent()`、`_invoke_cli()` | prompt、模型、effort、workspace | backend result、usage JSON | 以 `claude --output-format json -p` 调 Claude Code CLI，记录 token；网络 API 由 CLI 自身完成 | Claude Code CLI |
| `DAC26_DRC_Benchmark/evaluator/prepare_render_script.py` | Benchmark评测 | `main()` | repaired layout `.py` | render `.py` | 重写或补充 `layout.write(<gds>)` | Python 标准库 |
| `DAC26_DRC_Benchmark/evaluator/run_klayout_drc.py` | Benchmark评测 | `run_klayout_drc()` | GDS、`.lydrc` | `.lyrpt` | 用 KLayout batch 模式运行规则 deck | KLayout |
| `DAC26_DRC_Benchmark/evaluator/process_klayout_reports.py` | Benchmark评测 | `parse_lyrpt()`、`process_single_file()` | `.lyrpt`、layout 脚本 | `.drc.json` | 独立解析 KLayout XML 报告并做点违规 polygon 回填 | Python XML/JSON |
| `DAC26_DRC_Benchmark/evaluator/sanity_check.py` | Benchmark评测 | `sanity_check()` | 原始/修改 GDS 与脚本 | sanity JSON | 检查 top cell、非空、层/cell、边界、冻结层/实例和可选连通性 | KLayout `pya` |
| `DAC26_DRC_Benchmark/evaluator/check_connectivity.py` | Benchmark评测 | `check_connectivity()` | golden connectivity JSON、修改脚本、design type | connectivity JSON | 解析/展平金属与 via 图，比较黄金路径是否仍存在 | KLayout、图/并查集逻辑 |
| `DAC26_DRC_Benchmark/evaluator/score_repair.py` | Benchmark评测 | `score_repair()`、`main()` | 原始/新 DRC 报告、可信 agent meta | score JSON | 计算原始/剩余/修复/新增违规及 repair rate | Python XML/JSON |
| `DAC26_DRC_Benchmark/evaluator/merge_score_sanity.py` | Benchmark评测 | `main()` | score JSON、sanity JSON | 更新后的 score JSON | 合并结构完整性结果 | Python 标准库 |
| `DAC26_DRC_Benchmark/evaluator/merge_score_connectivity.py` | Benchmark评测 | `main()` | score JSON、connectivity JSON | 更新后的 score JSON | 合并可信连通性结果 | Python 标准库 |

## 关键边界

- `decompose_ctx()`、`apply_patch_to_text()`、`gate_leaf()`、`assemble()` 和 `run_block_drc_nested()` 都是函数；它们分别定义在 `decompose.py`、`patch_apply.py`、`gate.py`、`assemble.py` 和 `block_eval.py`。
- EvoDRC 内部评测服务于迭代决策：普通 unit 的 `gate.py` 只做 Patch 合法 envelope/非空和黄金连通性硬门控；共享目标可进入真实 crop DRC 竞赛；组装后 `block_eval.py` 做全块 DRC 与连通性复测。
- Benchmark 可信评测在 agent 阶段结束后由宿主断开容器网络、终止残留进程、注入 evaluator 与可信元数据，再独立重渲染、运行 KLayout、sanity、连通性和评分；其结果才是最终 Benchmark 分数。
- 当前根仓库 `agent/` 与子模块工作区中安装的核心 Python 实现逐文件相同；`agent/evodrc.conf` 不同。运行 `DAC26_DRC_Benchmark/src/evaluate_claude.sh` 时，实际挂载的是子模块工作区的 `agent/`，因此运行参数以该副本的配置为准。
