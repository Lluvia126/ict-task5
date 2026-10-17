# AI 实体抽取模块（招投标数据）

面向赛题五「面向行业数据的智能实体挖掘与关系建模」的 AI/NLP 抽取模块。本模块只负责 AI 抽取，不修改 FastAPI 后端与 Vue 前端。

## 目录结构

```
ai_extraction/
├── extractors/
│   ├── base.py                  # 抽取器基类（统一两条技术路线接口）
│   ├── deepseek_extractor.py    # 方案A：DeepSeek Prompt 抽取（当前基线）
│   └── transformer_extractor.py # 方案B：Transformer NER（接口预留，尚未实现）
├── preprocessing/
│   ├── cleaner.py               # preprocess_text 文本预处理
│   └── chunker.py               # chunk_text 文本分块
├── evaluation/
│   └── metrics.py               # evaluate 评估（P/R/F1、字段级准确率）
├── data/
│   ├── raw/                     # 原始数据
│   ├── processed/               # 抽取结果
│   └── validation/
│       └── validation.json      # 验证集（当前仅1条测试样例，非官方数据）
├── config.py                    # 配置（环境变量、字段、Prompt、目录）
└── batch_process.py             # batch_extract 批量处理
```

## 数据说明

`data/validation/validation.json` 当前仅含一条测试样例，**不是官方数据**，label 未经人工校验，不可用于声称任何准确率指标。后续需用真实官方数据建立验证集并人工校验后再做评估。

## 依赖

```bash
pip install openai            # 必需
pip install python-dotenv     # 可选：用于自动读取 .env
```

## 配置

复制 `.env.example` 为 `.env`，填入真实 `DEEPSEEK_API_KEY`。也可用系统环境变量设置。

## 运行

```bash
python test.py
```

## 技术路线

- 方案A（当前）：文本 → 预处理 → 分块 → DeepSeek Prompt → 7字段 JSON
- 方案B（后续）：文本 → Transformer NER → 实体识别 → 字段映射 → 结构化字段
