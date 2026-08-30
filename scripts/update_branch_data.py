"""每日抓取12個券商分點的買賣超資料（來源：富邦證券 fubon-ebrokerdj.fbs.com.tw），
直接改寫 tw-strategy-monitor 的 index.html 裡 SEED_DATA 中「分點追蹤」策略的
branches[].periods，然後 commit + push（GitHub Pages 會在 push 後自動重新建置）。

不寫入任何資料庫（不使用 Firestore）——資料的唯一存放處就是 index.html 本身。

Run manually, or as the daily scheduled task.
"""
import calendar
import datetime
import json
import os
import re
import subprocess

import requests

SITE_REPO = os.path.expanduser("~/tw-strategy-monitor")
SITE_INDEX = os.path.join(SITE_REPO, "index.html")

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}

ROW_RE = re.compile(
    r"GenLink2stk\('A?S?(\w+)','([^']+)'\).*?"
    r'<td class="t3n1"[^>]*>([\-\d,]+)</td>\s*'
    r'<td class="t3n1"[^>]*>([\-\d,]+)</td>\s*'
    r'<td class="t3n1"[^>]*>([\-\d,]+)</td>',
    re.S
)

# (branch_id, 顯示名稱, 富邦 a=, 富邦 b=)
BRANCHES = [
    ("khgt-8450", "康和證券", "8450", "8450"),
    ("fb-9661", "富邦新店", "9600", "9661"),
    ("yd-9801", "元大松江", "9800", "9801"),
    ("gt-8880", "國泰國泰", "8880", "8880"),
    ("kg-9275", "凱基三多", "9200", "9275"),
    ("kg-9217", "凱基松山", "9200", "9217"),
    ("gp-779z", "國票安和", "7790", "003700370039005a"),
    ("tx-9b25", "台新五權西", "9B00", "0039004200320035"),
    ("yd-984k", "元大館前", "9800", "003900380034004b"),
    ("hn-9300", "華南", "9300", "9300"),
    ("dyj-lz", "第一金路竹", "5380", "0035003300380050"),
    ("zf-zl", "兆豐中壢", "7000", "0037003000300062"),
    ("yf-nh", "永豐內湖", "9A00", "0039004100390067"),
]
PERIOD_ORDER = ['1', '5', '20', '3m', '6m']
PERIOD_LABEL = {'1': '近1日', '5': '近5日', '20': '近20日', '3m': '近三個月', '6m': '近半年'}


def months_ago(base_date, months):
    year = base_date.year
    month = base_date.month - months
    while month <= 0:
        month += 12
        year -= 1
    last_day = calendar.monthrange(year, month)[1]
    day = min(base_date.day, last_day)
    return datetime.date(year, month, day)


def fmt_date_param(d):
    return f"{d.year}-{d.month}-{d.day}"


def fmt_date(date_key):
    if not date_key or len(date_key) != 8:
        return date_key
    return f"{date_key[0:4]}/{date_key[4:6]}/{date_key[6:8]}"


def fmt_wan(amt_thousand):
    wan = amt_thousand / 10
    s = f"{abs(wan):,.1f}"
    return ("-" if wan < 0 else "") + s


def esc(s):
    return s.replace("\\", "\\\\").replace("'", "\\'")


def parse_rows(html):
    rows = []
    for m in ROW_RE.finditer(html):
        code, name, buy_amt, sell_amt, diff_amt = m.groups()
        rows.append({"code": code, "name": name, "diff": float(diff_amt.replace(',', ''))})
    return rows


def fetch_html(major, branch, extra_params):
    url = f"https://fubon-ebrokerdj.fbs.com.tw/z/zg/zgb/zgb0.djhtm?a={major}&b={branch}&c=B&{extra_params}"
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.encoding = 'big5'
    return r.text


def parse_from_html(text):
    date_m = re.search(r'資料日期[：:]\s*(\d{8})', text)
    date_key = date_m.group(1) if date_m else None
    buy_idx = text.find('買超')
    sell_idx = text.find('賣超', buy_idx + 1)
    buy_html = text[buy_idx:sell_idx]
    sell_html = text[sell_idx:]
    return date_key, parse_rows(buy_html), parse_rows(sell_html)


def fetch_period(major, branch, period, today, three_mo_start, six_mo_start):
    if period in ('1', '5', '20'):
        text = fetch_html(major, branch, f"d={period}")
    elif period == '3m':
        text = fetch_html(major, branch, f"e={fmt_date_param(three_mo_start)}&f={fmt_date_param(today)}")
    elif period == '6m':
        text = fetch_html(major, branch, f"e={fmt_date_param(six_mo_start)}&f={fmt_date_param(today)}")
    else:
        raise ValueError(period)
    return parse_from_html(text)


def fetch_all():
    today = datetime.date.today()
    three_mo_start = months_ago(today, 3)
    six_mo_start = months_ago(today, 6)

    result = {}
    checklist = []
    has_errors = False
    for bid, name, major, branch in BRANCHES:
        result[bid] = {}
        branch_ok = True
        branch_notes = []
        for p in PERIOD_ORDER:
            try:
                date_key, buy_rows, sell_rows = fetch_period(major, branch, p, today, three_mo_start, six_mo_start)
                if not buy_rows and not sell_rows:
                    raise ValueError("買超/賣超皆為空（可能是非交易日或網站暫時無資料）")
                buy10 = [[f"{r['name']} {r['code']}", fmt_wan(r['diff'])] for r in buy_rows[:10]]
                sell10 = [[f"{r['name']} {r['code']}", fmt_wan(r['diff'])] for r in sell_rows[:10]]
                result[bid][p] = {"updated": fmt_date(date_key), "buy": buy10, "sell": sell10}
            except Exception as e:
                branch_ok = False
                has_errors = True
                branch_notes.append(f"{PERIOD_LABEL[p]}失敗（{e}）")
                result[bid][p] = None  # 保留舊資料，不覆蓋

        if branch_ok:
            checklist.append(f"✅ {name}（{bid}）：5 種天期皆更新成功")
        else:
            checklist.append(f"❌ {name}（{bid}）：{'、'.join(branch_notes)}")

    return result, checklist, has_errors


# ---- 改寫 index.html ----

ID_LINE_RE = re.compile(r"^\s*id: '([^']+)',\s*$")
ITEM_RE = re.compile(r"\['((?:[^'\\]|\\.)*)','((?:[^'\\]|\\.)*)'\]")


def find_branch_positions(lines):
    positions = {}
    for i, line in enumerate(lines):
        m = ID_LINE_RE.match(line)
        if m:
            positions[m.group(1)] = i
    return positions


def find_periods_range(lines, id_idx):
    p_start = None
    for j in range(id_idx, id_idx + 10):
        if lines[j].strip() == 'periods: {':
            p_start = j
            break
    if p_start is None:
        raise RuntimeError("找不到 periods: { 起始行")
    depth = 0
    for j in range(p_start, len(lines)):
        depth += lines[j].count('{') - lines[j].count('}')
        if depth == 0:
            return p_start, j
    raise RuntimeError("找不到 periods 結尾")


def parse_existing_periods(lines, p_start, p_end):
    """從既有的 periods 區塊還原出 {period: {updated, buy, sell}}，供 fetch 失敗時 fallback 用。"""
    text = ''.join(lines[p_start:p_end + 1])
    out = {}
    for p in PERIOD_ORDER:
        m = re.search(r"'" + re.escape(p) + r"':\s*\{(.*?)\n          \},?\n\s*(?='|\})", text, re.S)
        if not m:
            # 嘗試涵蓋最後一個 period（沒有逗號、且緊接 periods 區塊結尾）
            m = re.search(r"'" + re.escape(p) + r"':\s*\{(.*?)\n          \}\s*\Z", text, re.S)
        if not m:
            continue
        block = m.group(1)
        updated_m = re.search(r"updated:\s*'([^']*)'", block)
        buy_m = re.search(r"buy:\s*\[(.*?)\],\s*sell:", block, re.S)
        sell_m = re.search(r"sell:\s*\[(.*?)\]\s*\Z", block, re.S)
        out[p] = {
            "updated": updated_m.group(1) if updated_m else None,
            "buy": [list(t) for t in ITEM_RE.findall(buy_m.group(1))] if buy_m else [],
            "sell": [list(t) for t in ITEM_RE.findall(sell_m.group(1))] if sell_m else [],
        }
    return out


def build_periods_block(periods):
    out = ["        periods: {\n"]
    for idx, p in enumerate(PERIOD_ORDER):
        pdata = periods[p]
        out.append(f"          '{p}': {{\n")
        out.append(f"            updated: '{pdata['updated']}',\n")
        out.append("            buy: [\n")
        for label, amt in pdata["buy"]:
            out.append(f"              ['{esc(label)}','{amt}'],\n")
        out.append("            ],\n")
        out.append("            sell: [\n")
        for label, amt in pdata["sell"]:
            out.append(f"              ['{esc(label)}','{amt}'],\n")
        out.append("            ]\n")
        out.append("          }\n" if idx == len(PERIOD_ORDER) - 1 else "          },\n")
    out.append("        }\n")
    return out


def apply_to_html(fetched):
    with open(SITE_INDEX, encoding="utf-8") as f:
        lines = f.readlines()

    positions = find_branch_positions(lines)
    for bid, _, _, _ in BRANCHES:
        if bid not in positions:
            print(f"⚠️ 在 index.html 找不到分點 id={bid}，略過")

    # 由後往前處理，避免行號被前面的替換打亂
    for bid in sorted((b for b in positions if b in fetched), key=lambda k: positions[k], reverse=True):
        id_idx = positions[bid]
        p_start, p_end = find_periods_range(lines, id_idx)
        existing = parse_existing_periods(lines, p_start, p_end)

        merged = {}
        for p in PERIOD_ORDER:
            fresh = fetched[bid].get(p)
            if fresh is not None:
                merged[p] = fresh
            elif p in existing:
                merged[p] = existing[p]
            else:
                raise RuntimeError(f"{bid} 的 {p} 既沒有新資料也沒有舊資料可用")

        new_block = build_periods_block(merged)
        lines[p_start:p_end + 1] = new_block

    with open(SITE_INDEX, "w", encoding="utf-8") as f:
        f.writelines(lines)


def git_commit_and_push():
    subprocess.run(["git", "add", "index.html"], cwd=SITE_REPO, check=True)
    diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=SITE_REPO)
    if diff.returncode == 0:
        print("No changes to commit.")
        return False

    today_str = datetime.date.today().strftime("%Y/%m/%d")
    subprocess.run(
        ["git", "-c", "user.email=momoyoung18@gmail.com", "-c", "user.name=momoyoung18-star",
         "commit", "-m", f"更新{today_str}分點買賣超資料"],
        cwd=SITE_REPO, check=True,
    )

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        result = subprocess.run(["git", "push", "origin", "main"], cwd=SITE_REPO)
        if result.returncode == 0:
            print("Pushed to tw-strategy-monitor.")
            return True
        if attempt == max_attempts:
            result.check_returncode()
        print(f"Push rejected (attempt {attempt}/{max_attempts}), rebasing onto latest remote and retrying...")
        subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=SITE_REPO, check=True)
    return True


def main():
    # 其他策略腳本（例如 ETF 持股異動）也會 push 到這個共用 repo，
    # 所以先同步到遠端最新狀態再編輯，避免用過舊的 base commit 修改。
    subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=SITE_REPO, check=True)

    fetched, checklist, has_errors = fetch_all()
    apply_to_html(fetched)
    has_changes = git_commit_and_push()

    print("\n--- 每日檢核清單 ---")
    for line in checklist:
        print(line)

    print()
    if has_errors:
        print("HAS_ERRORS")
    if has_changes:
        print("HAS_CHANGES")
    if not has_errors and not has_changes:
        print("NO_CHANGES")


if __name__ == "__main__":
    main()
