# Dedupe 合入 pipeline 方案（草案，未实施）

> 本方案仅描述合入设计，**尚未修改 pipeline.py**。
> 依据：`run_v4_1_dedup_reviewed/`（复核后规则）评估结果 —— Macro-F1 0.5170 → 0.5287，
> 7 字段 F1 全部提升，Matched 284 不变、Unmatched Gold 20 不变，无误删。

## 1. 插入位置

插入 `merge_chunk_results()` 末尾、`return merged` 之前：

```
legacy/structured 两条路径汇合
  → merge_chunk_results() 内部 products 汇总完成
  → 【新增】products = dedupe_products(products)   # 统一去重
  → return merged
```

## 2. 为什么这个位置安全

- 所有模式（legacy / structured）都汇聚到 `merge_chunk_results` 返回的 `merged["products"]`，
  在此处去重**只动合并产物**，不影响 chunk 抽取、position_trace、raw_responses 的可追溯性。
- 评估端 `read_pred` 只读 `result.products`，去重不改变任何字段 schema。
- 去重是纯函数（输入 products 列表，输出子集），失败可安全回退为原列表。

## 3. exact dedupe（Layer 1）

- 指纹 = 规范化后的 7 字段元组
  `(product_service_name, category, brand, spec_model, unit_price, quantity, total_price)`
- 规范化仅用于指纹比对，**不改写存储值**：
  - 文本：NFKC（全角→半角、中英文括号统一）+ 连续空白归一 + strip + lower
  - 数字：去千分位/空白/末尾"元"后可解析为 float 则归一（108.0 == 108），
    不做单位换算（不剥离"万"，避免语义改变）
- 指纹完全相同 → 保留第一条。

## 4. similar dedupe（Layer 2，复核后规则）

两条记录同时满足才允许合并：

1. **product_name 规范化后完全相等**（容忍空白/全半角差异）。
   不用模糊阈值 —— 长名称差 1 字（海温/海浪、第一包/第二包）即为不同实体。
2. **brand / spec_model / unit_price / quantity / total_price**
   每字段「相同或都为空」（一侧有值一侧为空 → 不合并）。
3. **category 冲突拦截**：两侧 category 均非空且不同（如 3标段/4标段）→ 禁止合并；
   一侧为空不拦截（互补情形常见且安全）。

聚类方式：**代表元聚类**。按字段完整度降序处理，每条只与簇代表比较，
不用并查集 —— 杜绝经全空/部分空"枢纽"记录的链式传递误并。

合并动作：**保留字段最完整的一条**（非空字段多者，并列取原序靠前者），
其余整条丢弃；**不做字段互补写回**。

## 5. 如何避免误合并（实测依据）

| 风险场景 | 防护 |
|---|---|
| 同名不同规格（配电柜 中电强能 vs ITC） | spec/brand 冲突 → 不合并 ✓ 实测未合并 |
| 同名不同价格（红外热成像仪 183900 vs 187000） | unit_price 冲突 → 不合并 ✓ 实测未合并 |
| 不同标段（鲜鸡蛋 3/4标段） | category 冲突拦截 ✓ reviewed 版已恢复 |
| 长名差 1 字（海温/海浪） | 名称必须完全相等 ✓ |
| 全空记录当枢纽链式误并 | 代表元聚类，不用并查集 ✓ |
| 连续表格同名续行 | 字段必须全部相等或都为空才合并 |

## 6. structured / legacy 分别处理

- **legacy 模式**：无 position 信息，直接对合并产物执行 Layer1 + Layer2（本次验证场景）。
- **structured 模式**：`_product_tuple` 已含 `position_id`，不同位置的同名字段组被
  视为不同实体 —— 结构化去重已较严格。Layer2 仍可统一执行，但需注意：
  structured 下同一实体若跨页续表被拆成两条（position 不同、字段互补），
  Layer2 因"一侧为空不合并"不会合并它们，**保守不处理**，留待续表合并逻辑解决。

## 7. position_trace 的优先使用

若记录带 position_trace（structured 模式）：

- 两条记录 position_id **相同** → 是同一位置重复对齐产物，优先按 Layer1 指纹去重。
- position_id **不同且不相邻** + 名称相同 + Layer2 字段全兼容 → 才可合并。
- position_id **相邻**且字段互补 → 疑似跨页续表，**不合并**（留给续表逻辑）。

legacy 模式无 position 信息，退化为纯字段规则（即本次验证规则）。

## 8. 冲突时默认保留哪条

- Layer1：保留**第一条**（完全等价，无所谓）。
- Layer2：保留**非空字段最多**的一条；并列时保留**原序靠前**的一条。
- 任何不确定（字段冲突、名称不完全相等、category 冲突）→ **默认都保留**（不合并）。

## 9. 回滚方案

- 实现为独立纯函数 `dedupe_products(products)`，pipeline 中以开关包裹：
  `if enable_dedupe: products = dedupe_products(products)`。
- 开关默认关闭合入，灰度验证通过后打开；出问题改回开关即回滚，
  不影响 raw_responses / position_trace 等原始数据。
- 离线脚本 `ai_extraction/dedupe_v41.py` 可独立复跑验证任何历史 run 目录。

## 10. 合入前检查清单

- [ ] 在 structured 模式样本上补测（当前 50 条全 legacy）
- [ ] 开关参数化 + 默认关闭
- [ ] 合入后用同一评估脚本复跑 V4.1 基线对比，确认 0 误删
