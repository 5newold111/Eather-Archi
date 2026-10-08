/** 支出カテゴリが4分類（敵1〜敵4）のどれかを返す */
function tierOf_(category) {
  for (let i = 0; i < TIERS.length; i++) {
    if (TIERS[i].categories.indexOf(category) >= 0) return TIERS[i].id;
  }
  return LEGACY_TIERS[category] || 2;
}

/** 収入・支出・差額と、4分類別・カテゴリ別の合計を計算する */
function summarize_(entries) {
  const s = { income: 0, expense: 0, balance: 0, business: 0, byTier: { 1: 0, 2: 0, 3: 0, 4: 0 }, byCategory: {} };
  entries.forEach(function (e) {
    if (e.type === '収入') {
      s.income += e.amount;
    } else {
      s.expense += e.amount;
      s.byTier[e.tier] += e.amount;
      s.byCategory[e.category] = (s.byCategory[e.category] || 0) + e.amount;
      s.business += Math.round(e.amount * e.business_ratio / 100);
    }
  });
  s.balance = s.income - s.expense;
  return s;
}

function shiftMonth_(yyyyMm, delta) {
  const y = Number(yyyyMm.slice(0, 4));
  const m = Number(yyyyMm.slice(5, 7)) - 1 + delta;
  const d = new Date(y, m, 1);
  return d.getFullYear() + '-' + ('0' + (d.getMonth() + 1)).slice(-2);
}
