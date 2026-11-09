# TileKernels → tilelang-ascend 算子移植任务书

---

## 一、任务背景

DeepSeek 开源的 TileKernels 是基于 TileLang 的算子库，其 Ascend 后端面向 **950（A5）** 芯片实现。我们的 tilelang-ascend 仓库面向 **910B/910C（A2/A3）**。本次任务是将其中 **6 个纯 SIMD、低难度算子**移植到 tilelang-ascend 上（A2）。

本次 6 个算子均**只涉及 fp32/bf16 数据类型，不涉及 FP8/FP4**（A2 硬件不支持），无格式障碍。

## 二、分工

| 实习生 | 算子 1（入门） | 算子 2 | 算子 3 | 合入目录 |
|---|---|---|---|---|
| **罗可嘉** | mhc/expand_to_mhc fwd+bwd | mhc/pre_apply_mix fwd+bwd | mhc/post_bwd | `examples/tile_kernels/mhc/` |
| **范志浩** | engram/fused_weight | quant/cast（fp32→bf16 随机舍入） | engram/grad_w_reduce | `examples/tile_kernels/engram/`（新建）、`examples/tile_kernels/quant/` |

两人各领一个算子家族（罗：mhc 流形超连接；范：engram 门控 + 量化 cast），语义连贯、互不重叠。

## 三、公共资料与规则（两人必读）

### 3.1 两个仓库

| 仓库 | 获取方式 | 用途 |
|---|---|---|
| TileKernels（参考实现来源） | `git clone https://github.com/deepseek-ai/TileKernels.git` | 读算子定义、A5 参考实现、测试用例 |
| tilelang-ascend（你的开发仓库） | 已有环境 | 写代码、跑测试、跑性能 |

**路径约定**：本文档中所有文件路径均为**相对各自仓库根目录**的路径（例：`tile_kernels/mhc/expand_asc.py` 指 TileKernels 仓库下的该文件；`examples/tile_kernels/mhc/` 指 tilelang-ascend 仓库下的该目录），请按你本机仓库实际存放位置自行拼接。

### 3.2 怎么找到一个算子的全部资料

TileKernels 中每个算子的资料**固定分布在 4 个位置**（以 `expand_to_mhc` 为例）：

| 资料 | 路径模板 | 内容 |
|---|---|---|
| ① 算子定义（host 接口） | `tile_kernels/<模块>/<算子名>_kernel.py` | 函数签名、shape/dtype 断言、分块参数选择逻辑 |
| ② Ascend 参考实现（A5） | `tile_kernels/<模块>/<算子名>_asc.py` | A5 上的 kernel 实现（**算法逻辑的正确答案**） |
| ③ CUDA 参考实现 | `tile_kernels/<模块>/<算子名>_cuda.py` | 帮助理解语义（可读性更好） |
| ④ 测试与精度参考 | `tests/<模块>/test_<算子名>.py` | 测试形状、torch 参考实现、容差标准 |

你的 6 个算子对应文件（直接照此找）：

| 算子 | ① kernel.py | ② asc.py | ④ test.py |
|---|---|---|---|
| expand_to_mhc | `tile_kernels/mhc/expand_kernel.py` | `tile_kernels/mhc/expand_asc.py` | `tests/mhc/test_expand.py` |
| pre_apply_mix | `tile_kernels/mhc/pre_apply_mix_kernel.py` | `tile_kernels/mhc/pre_apply_mix_asc.py` | `tests/mhc/test_pre_apply_mix.py` |
| post_bwd | `tile_kernels/mhc/post_kernel.py` | `tile_kernels/mhc/post_asc.py` | `tests/mhc/test_post.py` |
| fused_weight | `tile_kernels/engram/engram_fused_weight_kernel.py` | `tile_kernels/engram/engram_fused_weight_asc.py` | `tests/engram/test_engram_fused_weight.py` |
| cast | `tile_kernels/quant/cast_kernel.py` | `tile_kernels/quant/cast_asc.py` | `tests/quant/test_cast.py` |
| grad_w_reduce | `tile_kernels/engram/engram_grad_w_reduce_kernel.py` | `tile_kernels/engram/engram_grad_w_reduce_asc.py` | `tests/engram/test_engram_grad_w_reduce.py` |

### 3.3 移植范式（最重要的一节，先精读再动手）

**核心差异**：TileKernels 的 asc 实现使用主线 tilelang 的指令级 API（`T.SimdVF()` + `S.vld/S.vmul/S.vsts` 等向量指令），面向 A5；而 tilelang-ascend 使用**高层 API**：`T.Kernel(grid, is_npu=True)` + `T.alloc_ub`（UB 缓冲分配）+ `T.copy`（GM↔UB 搬运）+ `T.tile.*`（tile 级计算原语）。

**因此不能照抄 asc 代码，必须按语义用 T.tile.* 风格重写。**

**唯一已完成的等价移植范例（必读第一文件）**：
`examples/tile_kernels/mhc/head_compute_mix_kernel.py`（对应 TileKernels 的 mhc_head_compute_mix）
从中学习：
- `@tilelang.jit(pass_configs={...})` 装饰器与 PassConfig 配置
- `T.Kernel(grid_size, is_npu=True) as (cid, vid)` 核内双核编号
- `T.alloc_ub` UB 缓冲、`T.copy` 搬运、`T.set_flag/T.wait_flag` 流水线同步
- `T.tile.*` 计算原语的用法
- 同目录测试文件的写法（torch 参考对拍）

**其他风格参考**（按需读）：
- `examples/mhc_post/example_mhc_post.py`（mhc_post 前向，含 910B 性能调优，配套 `examples/mhc_post/benchmark.md`）
- `examples/activation/swi_glu_v2.py`（双缓冲、persistent 多核写法）
- `examples/normalization/rms_norm.py`（归约类写法）
- `examples/tile_kernels/quant/per_block_cast_lossless_kernel.py`（host 侧非对齐形状 pad 处理思路）

**T.tile.* 原语清单**（定义在 `tilelang/language/ascend_tile.py`，不确定有哪些原语时就翻这个文件）：
- 逐元素：`add/sub/mul/div/abs/exp/ln/sqrt/rsqrt/sigmoid/silu/round/pow/clamp/compare/select`
- 归约：`reduce_sum/reduce_max/reduce_min`、`block_reduce_*`、`wholereduce*`
- 位运算：`bitwise_and/bitwise_or/bitwise_xor/bitwise_not/bitwise_lshift/bitwise_rshift`
- 数据重排：`gather/gather_mask/broadcast/transpose`
- 类型转换：`cast`（支持 CAST_NONE/CAST_RINT/CAST_FLOOR/CAST_CEIL/CAST_ROUND/CAST_TRUNC/CAST_ODD 七种舍入模式）
- 位级重解释：`T.reinterpretcast(dst, src, casttype)`（定义在 `tilelang/language/ascend.py`，搜索函数名 `reinterpretcast`）
- 其他：`axpy`、`atomic_add`、`topk/sort32/merge_sort`、`fill`

### 3.4 每个算子的开发流程（固定节奏）

1. **读懂算子**：精读 ①②③④ 四个文件，先用 torch 在 NPU 上写出参考实现，与 ④ 的测试对拍，确认自己理解无误
2. **实现 kernel**：用 T.tile.* 风格写 tilelang-ascend kernel。可借鉴 asc 版的**分块策略**（h_blk 切分、num_stages 双缓冲级数、多核切分方式），但代码写法必须换成 T.alloc_ub + T.copy + T.tile.*
3. **精度验收**：把 ④ 的测试用例移植成同目录 `test_<算子名>.py`（pytest 可跑），形状/dtype/容差与原测试保持一致
4. **性能验收**：见 3.5
5. **交付**：kernel + 测试 + bench 脚本 + 报告段

### 3.5 性能验收标准

**计时工具**：`tilelang.profiler.do_bench`（`tilelang/profiler/bench.py`，基于 torch.npu.Event，自动 warmup + L2 cache flush，返回毫秒）：
```python
from tilelang.profiler import do_bench
t_ms = do_bench(lambda: my_kernel(x, o))   # 被测函数
```

**情形 A —— 该算子有 Ascend C（aclnn）版本**：性能基线 = 等价的 torch_npu 调用（底层下发到对应 aclnn 算子），用 do_bench 同时测两者。
**达标线：tilelang 耗时 ≤ aclnn 耗时 ÷ 0.6（即达到 AscendC 性能的 0.6 倍以上）**

**情形 B —— 无 aclnn 对应实现**：带宽利用率验收：
- 先测本机实测峰值带宽：大张量 `y.copy_(x)`（如 512MB）的有效带宽，记为 `BW_peak`
- 算子有效带宽 `BW_op = (所有输入读取字节 + 所有输出写入字节) ÷ 耗时`
- **达标线：`BW_op ≥ 0.7 × BW_peak`**；且应显著快于 torch eager 分解实现（ sanity check）

各算子的验收情形见下面各自任务详情。

### 3.6 交付物与合入规则

每个算子交付 4 件：
1. kernel 文件（`<算子名>_kernel.py`，含 host 侧 wrapper，风格参照 head_compute_mix_kernel.py）
2. `test_<算子名>.py`（精度测试，pytest 可跑）
3. bench 脚本（输出耗时、与基线对比、有效带宽）
4. 报告段（精度结果、性能数据、调优过程记录）

**合入规则**：
- 精度达标 + 性能达标 → `examples/tile_kernels/<模块>/`
- 精度达标 + 性能未达标 → `examples_experiment/tile_kernels/<模块>/`
- 目录：mhc 类 → `examples/tile_kernels/mhc/`（已存在）；engram 类 → `examples/tile_kernels/engram/`（新建）；cast → `examples/tile_kernels/quant/`（已存在）

---

## 四、罗可嘉任务详情（mhc 家族，3 个算子）

### 4.1 任务一：expand_to_mhc_fwd / expand_to_mhc_bwd（入门，★）

**功能语义**：mHC（流形超连接）流水线中，把单路 hidden 状态沿 mhc 维广播复制成多路。
- **fwd**：`x (n, hidden) bf16` → `o (n, mhc, hidden) bf16`，即每行复制 mhc 份
- **bwd**：`o_grad (n, mhc, hidden) bf16` → `x_grad (n, hidden) bf16`，即沿 mhc 维求和

**torch 参考**：
```python
o = x.unsqueeze(1).expand(n, mhc, hidden).contiguous()   # fwd
x_grad = o_grad.sum(dim=1)                                # bwd (覆盖写)
```

**TileKernels 资料**：`tile_kernels/mhc/expand_kernel.py`（注意 `_asc_fwd_h_blk` 的 UB 预算分块逻辑，fwd num_stages=7）、`expand_asc.py`、`tests/mhc/test_expand.py`。

**实现要点**：
- fwd 按 h_blk（hidden 切块，保证 UB 装得下）+ 双缓冲搬运；多核按 n 或 (n, h_blk) 切
- TileKernels 要求 hidden%128（fwd）/ %64（bwd）；建议支持非对齐 hidden（host 侧 pad，参考 `examples/tile_kernels/quant/per_block_cast_lossless_kernel.py` 的处理），先实现对齐版也行
- bwd 是沿 mhc 的归约，注意 bf16 读入转 fp32 累加再转回 bf16

**精度验收**：移植 `tests/mhc/test_expand.py` 用例对拍 torch 参考。

**性能验收（情形 A）**：
- fwd 基线：`o.copy_(x.unsqueeze(1).expand(n, mhc, hidden))`（aclnn Expand/copy 路径）
- bwd 基线：`o_grad.sum(dim=1)`（aclnnReduceSum 路径）
- 用 torch_npu 的 profiler 确认基线命中的 aclnn 算子名，报告里注明

### 4.2 任务二：mhc_pre_apply_mix_fwd / mhc_pre_apply_mix_bwd（★★）

**功能语义**：mHC 中把多路状态按 mix 权重加权收缩成单路（前向），及其反向。
- **fwd**：`x (n, mhc, hidden) bf16`，`mix (n, mhc) fp32` → `o (n, hidden) bf16`
  `o[n,h] = Σ_m mix[n,m] · x[n,m,h]`（fp32 累加）
- **bwd**：输入 `o_grad (n, hidden) bf16`、`x`、`mix`，输出/返回：
  - `x_grad[n,m,h] += mix[n,m] · o_grad[n,h]`（**累加到已有值上**，注意不是覆盖）
  - `mix_grad[n,m] = Σ_h o_grad[n,h] · x[n,m,h]`（新建张量返回）

**torch 参考**：
```python
o = torch.einsum("nm,nmh->nh", mix, x.float()).to(torch.bfloat16)
x_grad = x_grad + mix.unsqueeze(-1) * o_grad.float().unsqueeze(1)     # 累加
mix_grad = (o_grad.float().unsqueeze(1) * x.float()).sum(-1)
```

**TileKernels 资料注意**：`pre_apply_mix_kernel.py` 中 bwd 只传 4 个参数（o_grad, x, mix, x_grad），**mix_grad 是函数内新建并 return 的**（见 `pre_apply_mix_asc.py` 中 `mix_grad = T.empty(...)` 与函数末尾 `return mix_grad`），移植时保持这个接口。

**实现要点**：h_blk ≤ 6144、align 64（见 `pre_apply_mix_kernel.py:13-14`）；mix_grad 需要在 h_blk 循环里做跨块 fp32 累加（asc 版用 mix_grad_acc 累加器，最后一次性写出），注意精度。

**性能验收（情形 B）**：无 aclnn 直接对应。带宽利用率 ≥ 0.7×BW_peak；并对比 torch eager（`einsum` 或 `(x.float()*mix.unsqueeze(-1)).sum(1)`），应显著更快。

### 4.3 任务三：mhc_post_bwd（★★）

**功能语义**：mHC post 层的反向。前向已在本仓实现（`examples/mhc_post/example_mhc_post.py`，必读）：
```
out[n, mo, h] = post_layer_mix[n, mo] · x[n, h] + Σ_mi comb_res_mix[n, mi, mo] · residual[n, mi, h]
```
其中 x/residual/out 为 bf16，post/comb 为 fp32（**注意 comb 的索引顺序是 [mi, mo]**）。

**接口签名**（对齐 TileKernels `post_kernel.py` 的 mhc_post_bwd）：
```python
mhc_post_bwd(x, residual, post_layer_mix, comb_res_mix, d_o)
  # x: (n, hidden) bf16, residual: (n, mhc, hidden) bf16
  # post: (n, mhc) fp32, comb: (n, mhc, mhc) fp32, d_o: (n, mhc, hidden) bf16
  # 返回 (d_x, d_residual, d_post_layer_mix, d_comb_res_mix)
  # d_x/d_residual: bf16, d_post/d_comb: fp32
```
（TileKernels 原版接受 4D (S,T,...)，host wrapper 里 flatten 成 n 即可，参考 example_mhc_post.py 的做法。）

**四路梯度公式**（自行用 torch.autograd 验证推导）：
```
d_x[h]        = Σ_mo  post[mo] · d_o[mo, h]
d_res[mi, h]  = Σ_mo  comb[mi, mo] · d_o[mo, h]
d_post[mo]    = Σ_h   d_o[mo, h] · x[h]
d_comb[mi,mo] = Σ_h   res[mi, h] · d_o[mo, h]
```

**实现要点**：h_blk ≤ 4096、align 64；d_post/d_comb 是小归约（对 h 或对 (h)），d_x/d_res 是向量加权求和；可参照现有 fwd 的多核切分与 UB 规划。

**精度验收**：移植 `tests/mhc/test_post.py`（它用 `torch.autograd.backward` 做参考，直接照搬该思路）。

**性能验收（情形 B）**：带宽利用率 ≥ 0.7×BW_peak；对比 torch eager 的 autograd 分解实现。

---

## 五、范志浩任务详情（engram 2 个 + cast 1 个）

### 5.1 任务一：fused_weight（入门，★）

**功能语义**：Engram 门控的预融合权重——两个 RMSNorm 权重逐元素相乘，为后续 gate fwd/bwd 准备：
```
weight_hidden (hc_mult, hidden) bf16  ×  weight_embed (hc_mult, hidden) bf16
→ weight_fused (hc_mult, hidden) fp32
```
（bf16 读入 → fp32 计算 → fp32 输出）

**torch 参考**：`weight_hidden.float() * weight_embed.float()`

**TileKernels 资料**：`tile_kernels/engram/engram_fused_weight_kernel.py`、`engram_fused_weight_asc.py`、`tests/engram/test_engram_fused_weight.py`。

**性能验收（情形 A）**：基线 = `torch.mul(a.float(), b.float())`（aclnnMul 路径）。注意该算子数据量小（典型 hc_mult=4, hidden=7168），单次耗时在微秒级，bench 时用 do_bench 取中位数，测试形状建议覆盖 hidden 2048/7168/16384 与 hc_mult 4/8。

### 5.2 任务二：cast（fp32→bf16 随机舍入，★★）

**功能语义**：把连续 fp32 张量转成 bf16，带**随机舍入（Stochastic Rounding）**。TileKernels 的 Ascend 版**仅支持这一种组合**（fp32→bf16 + SR，见 `cast_kernel.py:43-46` 的断言），n 需 %4。

**关键背景（务必先读 `cast_asc.py` 理解）**：它的 SR **不依赖随机数发生器**，而是用"输入值 + 元素位置"的**整数哈希**生成伪随机位：
```
rbits_hash: 对 4 个 fp32 的 bit 做 乘魔数(0x5671D42B 等) + 异或移位混合(>>23/^, >>16/^, >>11/^) + 与位置 base_e 混合
stochastic_round: 取 fp32 bits 的高 16 位 hi 和低 16 位 lo；若 hash16 < lo 则 hi+1 否则 hi（即按被丢弃尾数位的比例概率进位）
```
这是标准 SR 语义，**完全可用 tilelang-ascend 现有原语移植**：`T.reinterpretcast`（fp32↔uint32 位级重解释，见 `tilelang/language/ascend.py` 的 `reinterpretcast` 函数）+ `T.tile.bitwise_*` + `compare/select`。

**两个里程碑**：
- **M1（基础）**：普通 fp32→bf16 cast（`T.tile.cast` + CAST_RINT 舍入模式），与 `x.to(torch.bfloat16)`（aclnnCast 路径）对拍精度并做性能对比（情形 A，达标线 0.6×）
- **M2（完整）**：hash-SR 版。精度验收方式参照 `tests/quant/test_cast.py` 中 stochastic_rounding 分支（统计验收：SR 结果应无偏，可用大样本均值/分位数校验；hash 算法照抄 cast_asc.py 保证同种子可复现语义）

### 5.3 任务三：grad_w_reduce（★★）

**功能语义**：Engram 门控反向的权重梯度收尾——把按核产生的 partial 梯度归约，并结合融合权重做链式拆分，**原地累加**到两个权重梯度上：
```
grad_w_partial: (num_persistent_blocks, hc_mult, hidden) fp32
weight_hidden / weight_embed: (hc_mult, hidden) bf16
grad_weight_hidden / grad_weight_embed: (hc_mult, hidden) fp32   ← 原地累加 (+=)

s = grad_w_partial.sum(dim=0)                    # 跨 blocks 归约
grad_weight_hidden += s * weight_embed.float()   # 链式拆分: wh = fused/wh·we ⇒ dwh ∝ we
grad_weight_embed   += s * weight_hidden.float()
```

**接口签名**（对齐 `engram_grad_w_reduce_kernel.py`，返回 None，输出靠 in-place 修改）：
```python
grad_w_reduce(grad_w_partial, weight_hidden, weight_embed,
              grad_weight_hidden, grad_weight_embed)   # 后两个参数被原地累加
```

**torch 参考**：
```python
s = grad_w_partial.sum(0)
grad_weight_hidden += s * weight_embed.float()
grad_weight_embed   += s * weight_hidden.float()
```

**实现要点**：
- 两段式归约：先跨 num_persistent_blocks 求和（可用多核 partial + 二段归约，或单核串行块累加，数据量小），再逐元素乘加
- **注意 in-place 累加语义**：kernel 需要读出 grad_weight_* 的旧值再累加写回，测试中要专门验证"累加"而不是"覆盖"（先给非零初值再调用）

**精度验收**：移植 `tests/engram/test_engram_grad_w_reduce.py`。

**性能验收（情形 B）**：无 aclnn 直接对应（等价 torch 分解是 sum+mul+add 三个算子）。带宽利用率 ≥ 0.7×BW_peak；并对比三算子分解的 torch eager 总耗时。

---

## 六、共同注意事项

1. **精度是第一门槛**：任何算子精度不过关不算完成；性能达标与否决定合入 `examples/` 还是 `examples_experiment/`
2. **不要照抄 asc 代码**：语言层不同（SimdVF/S.* vs T.tile.*），照抄必然编译失败；抄"分块/缓冲/流水"的**策略**，重写**代码**
3. **形状对齐**：TileKernels Ascend 版普遍要求 hidden%128 对齐；tilelang-ascend 版建议至少支持 64 对齐，非对齐用 host pad（参考 per_block_cast_lossless_kernel.py）
4. **dtype 细节**：注意 bf16 读入后转 fp32 计算/累加（混合精度规范），输出再转回目标 dtype，参照各 asc 版的 cast 位置
5. **遇到问题**：先翻 `tilelang/language/ascend_tile.py` 确认原语是否存在及用法；再看 `examples/` 下相近算子的用法；再查 TileKernels 对应 cuda 实现理解语义
6. **每周汇报**：每完成一个算子汇报一次（精度数据 + 性能对比 + 遇到的问题）
