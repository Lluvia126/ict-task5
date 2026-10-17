from ..config import PROJECT_FIELDS, PROJECT_LIST_FIELDS, PRODUCT_FIELDS

ALL_FIELDS = PROJECT_FIELDS + PROJECT_LIST_FIELDS + PRODUCT_FIELDS


def _norm(v):
    """归一化：None/空 -> ""；列表 -> 排序元组；其余 -> strip 后字符串。"""
    if v is None:
        return ""
    if isinstance(v, list):
        return tuple(sorted(_norm(x) for x in v if x not in (None, "")))
    return str(v).strip()


def evaluate(predictions, labels):
    """计算字段级准确率、Precision / Recall / F1。

    predictions, labels: list[dict]，结构同抽取结果（含 products 数组）。
    项目级字段直接比较；产品按索引匹配后逐字段比较。采用精确匹配。
    """
    n = len(labels)
    if n == 0:
        return {"field_accuracy": {}, "precision": 0.0, "recall": 0.0, "f1": 0.0}

    field_total = {f: 0 for f in ALL_FIELDS}
    field_correct = {f: 0 for f in ALL_FIELDS}
    tp = fp = fn = 0

    for pred, gold in zip(predictions, labels):
        # 项目级字段
        for f in PROJECT_FIELDS + PROJECT_LIST_FIELDS:
            field_total[f] += 1
            p = _norm(pred.get(f))
            g = _norm(gold.get(f))
            if g != "" and g != ():
                if p == g:
                    field_correct[f] += 1
                    tp += 1
                else:
                    fn += 1
            else:
                if p != "" and p != ():
                    fp += 1

        # 产品级字段（按索引匹配）
        pred_ps = pred.get("products") or []
        gold_ps = gold.get("products") or []
        for idx, gp in enumerate(gold_ps):
            pp = pred_ps[idx] if idx < len(pred_ps) else {}
            for f in PRODUCT_FIELDS:
                field_total[f] += 1
                p = _norm(pp.get(f))
                g = _norm(gp.get(f))
                if g != "":
                    if p == g:
                        field_correct[f] += 1
                        tp += 1
                    else:
                        fn += 1
                else:
                    if p != "":
                        fp += 1

    field_accuracy = {
        f: round(field_correct[f] / field_total[f], 4)
        for f in ALL_FIELDS if field_total[f] > 0
    }
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)
          if (precision + recall) else 0.0)

    return {
        "field_accuracy": field_accuracy,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }
