import akshare as ak
import requests
import pandas as pd
import json
import re
import time
from tqdm import tqdm
import fitz  # PyMuPDF
from curl_cffi import requests as curl_requests


BASE_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://data.eastmoney.com/"
}


def parse_eastmoney_response(text):
    """
    兼容普通 JSON 和 JSONP 返回格式
    """
    text = text.strip()

    if not text.startswith("{"):
        match = re.search(r"\((.*)\)\s*;?$", text, re.S)
        if match:
            text = match.group(1)

    return json.loads(text)


def get_with_retry(
    url,
    params,
    headers,
    max_retries=5,
    sleep_seconds=1,
    timeout=(5, 30)
):
    """
    带重试机制的 requests.get

    timeout=(5, 30) 表示：
    5 秒连接超时
    30 秒读取超时
    """
    last_error = None

    for i in range(max_retries):
        try:
            response = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=timeout
            )
            response.raise_for_status()
            return response

        except Exception as e:
            last_error = e
            print(f"第 {i + 1} 次请求失败，准备重试：{e}")
            time.sleep(sleep_seconds * (i + 1))

    raise last_error


def fetch_reduce_records_one_stock(
    stock_code,
    notice_start="2026-02-26",
    notice_end="2026-05-25",
    only_5pct_holder=True,
    page_size=50,
    sleep_seconds=0.5,
    max_retries=5
):
    """
    抓取单只股票的股东减持记录，并筛选：
    1. 公告日期在指定区间内
    2. 减持前持股比例 >= 5%

    使用字段：
    AFTER_CHANGE_RATE = 本次减持占总股本比例
    HOLD_RATIO = 减持后持股比例

    判断逻辑：
    减持前持股比例 = HOLD_RATIO + abs(AFTER_CHANGE_RATE)
    """

    stock_code = str(stock_code).zfill(6)

    notice_start_dt = pd.to_datetime(notice_start)
    notice_end_dt = pd.to_datetime(notice_end)

    all_rows = []
    page_number = 1
    total_pages = None

    while True:
        params = {
            "sortColumns": "END_DATE,SECURITY_CODE,EITIME",
            "sortTypes": "-1,-1,-1",
            "pageSize": page_size,
            "pageNumber": page_number,
            "reportName": "RPT_SHARE_HOLDER_INCREASE",
            "quoteColumns": "f2~01~SECURITY_CODE~NEWEST_PRICE,f3~01~SECURITY_CODE~CHANGE_RATE_QUOTES",
            "quoteType": "0",
            "columns": "ALL",
            "source": "WEB",
            "client": "WEB",
            "filter": f'(SECURITY_CODE="{stock_code}")(DIRECTION="减持")'
        }

        try:
            response = get_with_retry(
                BASE_URL,
                params=params,
                headers=HEADERS,
                max_retries=max_retries,
                sleep_seconds=1,
                timeout=(5, 30)
            )

            data = parse_eastmoney_response(response.text)

        except Exception as e:
            print(f"{stock_code} 第 {page_number} 页请求失败，已重试 {max_retries} 次：{e}")
            raise e

        result = data.get("result")
        if not result:
            break

        rows = result.get("data", [])
        total_pages = result.get("pages", 0)

        if not rows:
            break

        all_rows.extend(rows)

        if page_number >= total_pages:
            break

        page_number += 1
        time.sleep(sleep_seconds)

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)

    keep_cols = [
        "SECURITY_CODE",
        "SECURITY_NAME_ABBR",
        "NOTICE_DATE",
        "HOLDER_NAME",
        "CHANGE_NUM_SYMBOL",
        "AFTER_CHANGE_RATE",
        "HOLD_RATIO",
        "START_DATE",
        "END_DATE"
    ]

    missing_cols = [col for col in keep_cols if col not in df.columns]
    if missing_cols:
        print(f"{stock_code} 缺少字段：{missing_cols}")
        return pd.DataFrame()

    df = df[keep_cols].copy()

    # 日期处理
    df["NOTICE_DATE"] = pd.to_datetime(df["NOTICE_DATE"], errors="coerce")
    df["START_DATE"] = pd.to_datetime(df["START_DATE"], errors="coerce")
    df["END_DATE"] = pd.to_datetime(df["END_DATE"], errors="coerce")

    # 数值处理
    numeric_cols = [
        "CHANGE_NUM_SYMBOL",
        "AFTER_CHANGE_RATE",
        "HOLD_RATIO"
    ]

    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # 减持股数，原始值通常是负数，取绝对值方便理解
    df["REDUCE_NUM"] = df["CHANGE_NUM_SYMBOL"].abs()

    # 本次减持占总股本比例
    df["REDUCE_RATIO_TOTAL"] = df["AFTER_CHANGE_RATE"].abs()

    # 减持前持股比例 = 减持后持股比例 + 本次减持占总股本比例
    df["BEFORE_HOLD_RATIO_EST"] = (
        df["HOLD_RATIO"].fillna(0)
        + df["REDUCE_RATIO_TOTAL"].fillna(0)
    )

    # 是否为减持前持股 5% 以上股东
    df["IS_5PCT_HOLDER"] = df["BEFORE_HOLD_RATIO_EST"] >= 5

    # 筛选公告日期区间
    df = df[
        (df["NOTICE_DATE"] >= notice_start_dt)
        & (df["NOTICE_DATE"] <= notice_end_dt)
    ].copy()

    # 筛选 5% 以上股东
    if only_5pct_holder:
        df = df[df["IS_5PCT_HOLDER"]].copy()

    if df.empty:
        return pd.DataFrame()

    # 日期转成 yyyy-mm-dd
    df["NOTICE_DATE"] = df["NOTICE_DATE"].dt.date
    df["START_DATE"] = df["START_DATE"].dt.date
    df["END_DATE"] = df["END_DATE"].dt.date

    # 中文列名
    df = df.rename(columns={
        "SECURITY_CODE": "股票代码",
        "SECURITY_NAME_ABBR": "股票简称",
        "NOTICE_DATE": "公告日期",
        "HOLDER_NAME": "减持股东",
        "CHANGE_NUM_SYMBOL": "减持股数_原始值",
        "REDUCE_NUM": "减持股数",
        "AFTER_CHANGE_RATE": "减持占总股本比例",
        "HOLD_RATIO": "减持后持股比例",
        "BEFORE_HOLD_RATIO_EST": "减持前持股比例_估算",
        "IS_5PCT_HOLDER": "是否5%以上股东",
        "START_DATE": "减持开始日",
        "END_DATE": "减持结束日"
    })

    df = df[
        [
            "股票代码",
            "股票简称",
            "公告日期",
            "减持股东",
            "减持股数_原始值",
            "减持股数",
            "减持占总股本比例",
            "减持后持股比例",
            "减持前持股比例_估算",
            "是否5%以上股东",
            "减持开始日",
            "减持结束日"
        ]
    ]

    return df

# 1. 获取中证1000当前成分股
df_index = ak.index_stock_cons_weight_csindex(symbol="000852")

pool = df_index[["成分券代码", "成分券名称"]].copy()
pool["成分券代码"] = pool["成分券代码"].astype(str).str.zfill(6)

#print(pool)
#print(pool.head())
#print(pool.shape)


# 2. 批量抓取1000只股票的减持信息
all_results = []
failed_codes = []

for _, row in tqdm(pool.iterrows(), total=len(pool)):
    code = row["成分券代码"]

    try:
        temp = fetch_reduce_records_one_stock(
            stock_code=code,
            notice_start="2026-02-26",
            notice_end="2026-05-25",
            only_5pct_holder=True,
            page_size=50,
            sleep_seconds=0.5,
            max_retries=5
        )

        if not temp.empty:
            all_results.append(temp)

    except Exception as e:
        print(f"{code} 最终抓取失败：{e}")
        failed_codes.append(code)

    # 每只股票之间暂停一下，降低被接口限速或超时的概率
    time.sleep(0.5)


# 3. 合并结果
if all_results:
    result_df = pd.concat(all_results, ignore_index=True)

    # 去重
    result_df = result_df.drop_duplicates(
        subset=[
            "股票代码",
            "公告日期",
            "减持股东",
            "减持股数_原始值",
            "减持开始日",
            "减持结束日"
        ],
        keep="first"
    )
else:
    result_df = pd.DataFrame()

#print(result_df.head())
#print(result_df.shape)


# 4. 保存失败股票代码，方便之后补爬
failed_df = pd.DataFrame({"股票代码": failed_codes})

#print("失败股票数量：", len(failed_codes))
#print(failed_codes)

# 单独补爬这两个失败的股票
# rerun_codes = ["300406", "603013"]

# rerun_results = []

# for code in rerun_codes:
#     print(f"正在补爬 {code} ...")

#     try:
#         temp = fetch_reduce_records_one_stock(
#             stock_code=code,
#             notice_start="2026-02-26",
#             notice_end="2026-05-25",
#             only_5pct_holder=True,
#             page_size=50,
#             sleep_seconds=0.5
#         )

#         if not temp.empty:
#             rerun_results.append(temp)
#             print(f"{code} 补爬成功，得到 {len(temp)} 条记录")
#         else:
#             print(f"{code} 没有符合条件的记录")

#     except Exception as e:
#         print(f"{code} 补爬失败：{e}")

#     time.sleep(1)


# # 合并补爬结果
# if rerun_results:
#     rerun_df = pd.concat(rerun_results, ignore_index=True)
# else:
#     rerun_df = pd.DataFrame()

# print("补爬结果：")
# print(rerun_df)
# print(rerun_df.shape)

ANN_LIST_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"
ANN_CONTENT_URL = "https://np-cnotice-stock.eastmoney.com/api/content/ann"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://data.eastmoney.com/"
}

PDF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/148.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "image/avif,image/webp,image/apng,*/*;q=0.8,"
              "application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


# =========================================================
# 2. 通用请求函数
# =========================================================

def parse_json_or_jsonp(text):
    """
    兼容普通 JSON 和 JSONP。
    """
    text = text.strip()

    if not text.startswith("{"):
        match = re.search(r"\((.*)\)\s*;?$", text, re.S)
        if match:
            text = match.group(1)

    return json.loads(text)


def get_json_with_retry(
    url,
    params=None,
    headers=None,
    max_retries=5,
    sleep_seconds=1,
    timeout=(5, 30)
):
    """
    请求 JSON 接口，带重试。
    """
    last_error = None

    for i in range(max_retries):
        try:
            response = requests.get(
                url,
                params=params,
                headers=headers or HEADERS,
                timeout=timeout
            )
            response.raise_for_status()
            return parse_json_or_jsonp(response.text)

        except Exception as e:
            last_error = e
            print(f"第 {i + 1} 次请求失败，准备重试：{e}")
            time.sleep(sleep_seconds * (i + 1))

    raise last_error


def get_text_series(df, col):
    """
    安全获取 DataFrame 某一列的字符串 Series。
    如果列不存在，返回空字符串 Series。
    """
    if col in df.columns:
        return df[col].fillna("").astype(str)
    else:
        return pd.Series([""] * len(df), index=df.index)


def limit_text_length(text, max_len):
    """
    限制文本长度。
    不加省略号，确保最终字符数不超过 max_len。
    """
    if text is None:
        return ""

    text = str(text).strip()

    if not text:
        return ""

    return text[:max_len]


# =========================================================
# 3. 获取公告列表和公告 PDF 链接
# =========================================================

def fetch_announcement_list(stock_code, page_size=200, max_pages=2):
    """
    根据股票代码获取东方财富公告列表。

    修改点：
    1. 东方财富单页实际最多返回 100 条；
    2. 即使 page_size 设置为 200，也要尝试 page_index=1 和 page_index=2；
    3. 不再使用 len(rows) < page_size 来提前停止；
    4. 默认只尝试前 2 页，对应最多 200 条公告。
    """
    stock_code = str(stock_code).zfill(6)

    all_rows = []

    # 接口单页实际最多 100 条，所以实际请求最多设为 100
    actual_page_size = min(int(page_size), 100)

    # 固定尝试 page_index = 1 和 2
    for page_index in range(1, max_pages + 1):
        params = {
            "sr": "-1",
            "page_size": actual_page_size,
            "page_index": page_index,
            "ann_type": "A",
            "client_source": "web",
            "stock_list": stock_code,
            "f_node": "0",
            "s_node": "0"
        }

        try:
            data = get_json_with_retry(
                ANN_LIST_URL,
                params=params,
                headers=HEADERS,
                max_retries=5,
                sleep_seconds=1,
                timeout=(5, 30)
            )
        except Exception as e:
            print(f"{stock_code} 公告列表第 {page_index} 页失败：{e}")
            continue

        rows = data.get("data", {}).get("list", [])

        if rows:
            all_rows.extend(rows)

        time.sleep(0.3)

    if not all_rows:
        return pd.DataFrame()

    df_ann = pd.DataFrame(all_rows)

    # 去重，防止接口 page_index=1 和 2 返回重复公告
    if "art_code" in df_ann.columns:
        df_ann = df_ann.drop_duplicates(subset=["art_code"], keep="first")
    else:
        df_ann = df_ann.drop_duplicates(keep="first")

    df_ann["notice_date_dt"] = pd.to_datetime(
        df_ann.get("notice_date"),
        errors="coerce"
    )

    return df_ann


def get_pdf_url_from_art_code(art_code):
    """
    根据 art_code 获取公告正文接口里的 PDF attach_url。
    """
    if pd.isna(art_code) or art_code == "":
        return ""

    params = {
        "art_code": art_code,
        "client_source": "web",
        "page_index": "1"
    }

    try:
        data = get_json_with_retry(
            ANN_CONTENT_URL,
            params=params,
            headers=HEADERS,
            max_retries=5,
            sleep_seconds=1,
            timeout=(5, 30)
        )
    except Exception as e:
        print(f"art_code={art_code} 获取公告内容失败：{e}")
        return ""

    content_data = data.get("data", {})
    attach_list = content_data.get("attach_list", [])

    if isinstance(attach_list, list) and len(attach_list) > 0:
        first_attach = attach_list[0]

        pdf_url = first_attach.get("attach_url", "")

        if not pdf_url:
            pdf_url = first_attach.get("attach_url_web", "")

        return pdf_url or ""

    return ""


# =========================================================
# 4. 下载 PDF 并提取原文
# =========================================================

def extract_text_from_pdf_url(pdf_url):
    """
    下载 PDF 并提取文字。
    失败则返回空字符串。
    """
    if not pdf_url:
        return ""

    try:
        if curl_requests is not None:
            response = curl_requests.get(
                pdf_url,
                headers=PDF_HEADERS,
                impersonate="chrome",
                timeout=30
            )
            content = response.content
        else:
            response = requests.get(
                pdf_url,
                headers=PDF_HEADERS,
                timeout=30
            )
            content = response.content

        if not content.startswith(b"%PDF"):
            print(f"返回的不是 PDF：{pdf_url}")
            return ""

        doc = fitz.open(stream=content, filetype="pdf")

        text_list = []
        for page in doc:
            text_list.append(page.get_text())

        doc.close()

        return "\n".join(text_list)

    except Exception as e:
        print(f"PDF 提取失败：{pdf_url}，原因：{e}")
        return ""


def clean_extracted_text(text):
    """
    清洗 PDF 提取出来的文本。
    """
    if not text:
        return ""

    text = str(text)
    text = text.replace("\u3000", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n+", "\n", text)

    return text.strip()


# =========================================================
# 5. 查找减持公告和预披露公告
# =========================================================

def find_reduce_announcements(df_ann, notice_date):
    """
    找减持公告。

    条件：
    1. notice_date 和 result_df 中的公告日期一致
    2. title/title_ch 含有 “减持” 或 “股东权益” 或 “股东” 或 “权益变动”
    3. 如果有多条，全部返回
    """
    if df_ann.empty:
        return []

    notice_date = pd.to_datetime(notice_date).normalize()

    temp = df_ann.copy()

    temp["notice_date_norm"] = pd.to_datetime(
        temp["notice_date"],
        errors="coerce"
    ).dt.normalize()

    title_all = (
        get_text_series(temp, "title_ch")
        + get_text_series(temp, "title")
    )

    mask = (
        (temp["notice_date_norm"] == notice_date)
        & title_all.str.contains(r"减持|股东权益|股东|权益变动", na=False, regex=True)
    )

    candidates = temp[mask].copy()

    if candidates.empty:
        return []

    if "display_time" in candidates.columns:
        candidates = candidates.sort_values("display_time")
    elif "sort_date" in candidates.columns:
        candidates = candidates.sort_values("sort_date")

    return candidates.to_dict("records")


def find_predisclosure_announcement(df_ann, start_date, max_days_before=60):
    """
    找预披露公告。

    条件：
    1. notice_date <= 减持开始日
    2. notice_date >= 减持开始日 - 60 天
    3. title/title_ch 含有 “预披露”
    4. 如果有多条，取 notice_date 最接近减持开始日的一条
    """
    if df_ann.empty:
        return None

    start_date = pd.to_datetime(start_date).normalize()
    min_date = start_date - pd.Timedelta(days=max_days_before)

    temp = df_ann.copy()

    temp["notice_date_norm"] = pd.to_datetime(
        temp["notice_date"],
        errors="coerce"
    ).dt.normalize()

    title_all = (
        get_text_series(temp, "title_ch")
        + get_text_series(temp, "title")
    )

    mask = (
        (temp["notice_date_norm"] <= start_date)
        & (temp["notice_date_norm"] >= min_date)
        & title_all.str.contains("预披露", na=False)
    )

    candidates = temp[mask].copy()

    if candidates.empty:
        return None

    candidate_title_all = (
        get_text_series(candidates, "title_ch")
        + get_text_series(candidates, "title")
    )

    better = candidates[
        candidate_title_all.str.contains("减持", na=False)
        & candidate_title_all.str.contains("预披露", na=False)
    ].copy()

    if not better.empty:
        candidates = better

    candidates = candidates.sort_values("notice_date_norm", ascending=False)

    return candidates.iloc[0].to_dict()


# =========================================================
# 6. 从 PDF 原文提取减持原因和股东类型
# =========================================================

def extract_reduce_reason(text):
    """
    从预披露公告原文中提取减持原因。

    规则：
    从 “减持原因 / 减持股份原因 / 本次减持的原因 / 拟减持原因”
    后面的冒号或“为”之后开始，提取到第一个中文句号“。”。
    不因为换行停止。

    限制：
    减持原因最多保留 50 字。
    """
    if not text:
        return ""

    text = clean_extracted_text(text)

    keywords = [
        "减持原因",
        "减持股份原因",
        "本次减持的原因",
        "拟减持原因"
    ]

    for kw in keywords:
        pattern = rf"{kw}\s*[:：为]?\s*(.*?。)"
        match = re.search(pattern, text, re.S)

        if match:
            reason = match.group(1).strip()

            # 去掉换行和多余空白
            reason = re.sub(r"\s+", "", reason)

            # 去掉开头残留符号
            reason = re.sub(r"^[：:为\s]+", "", reason)

            # 减持原因限制不超过 50 字
            reason = limit_text_length(reason, 50)

            return reason.strip()

    return ""


def extract_shareholder_type_from_reduce_text(text):
    """
    从减持公告 PDF 原文中提取股东类型。

    规则：
    匹配标题中的：
    关于xxx拟减持......
    关于xxx减持......
    关于xxx权益变动......

    只提取 xxx。
    关键词后面可以继续有其他字段。

    限制：
    股东类型最多保留 20 字。
    """
    if not text:
        return ""

    # 标题通常在 PDF 前面，截取前 3000 字减少误匹配
    text = text[:3000]

    # 去掉换行和空白，避免标题被 PDF 拆开
    text = re.sub(r"\s+", "", text)

    patterns = [
        r"关于(.{1,80}?)(?:拟减持)",
        r"关于(.{1,80}?)(?:减持)",
        r"关于(.{1,80}?)(?:权益变动)"
    ]

    for pattern in patterns:
        match = re.search(pattern, text)

        if match:
            holder_type = match.group(1).strip()

            # 清理可能的标点
            holder_type = holder_type.strip("：:，,。；;的 ")

            if holder_type:
                # 股东类型限制不超过 20 字
                holder_type = limit_text_length(holder_type, 20)
                return holder_type

    return ""


# =========================================================
# 7. 对 result_df 的每一行补充信息
# =========================================================

def enrich_one_reduce_row(row, ann_cache, content_cache, pdf_text_cache):
    """
    对 result_df 中的一行补充：
    1. 股东类型
    2. 减持原因
    3. 减持公告链接
    4. 预披露公告链接
    """

    stock_code = str(row["股票代码"]).zfill(6)
    notice_date = row["公告日期"]
    start_date = row["减持开始日"]

    result = {
        "股东类型": "",
        "减持原因": "",
        "减持公告链接": "",
        "预披露公告链接": ""
    }

    # 1. 获取公告列表，使用缓存
    if stock_code in ann_cache:
        df_ann = ann_cache[stock_code]
    else:
        df_ann = fetch_announcement_list(stock_code)
        ann_cache[stock_code] = df_ann
        time.sleep(0.3)

    if df_ann.empty:
        return result

    # 2. 找所有减持公告
    reduce_anns = find_reduce_announcements(df_ann, notice_date)

    reduce_pdf_urls = []

    for ann in reduce_anns:
        art_code = ann.get("art_code", "")

        if not art_code:
            continue

        if art_code in content_cache:
            pdf_url = content_cache[art_code]
        else:
            pdf_url = get_pdf_url_from_art_code(art_code)
            content_cache[art_code] = pdf_url
            time.sleep(0.2)

        if pdf_url:
            reduce_pdf_urls.append(pdf_url)

    # 去重但保留顺序
    reduce_pdf_urls = list(dict.fromkeys(reduce_pdf_urls))

    # 多条减持公告链接全部保存，用逗号分隔
    result["减持公告链接"] = ", ".join(reduce_pdf_urls)

    # 3. 股东类型：只有一条减持公告链接时，才从该减持公告 PDF 中提取
    if len(reduce_pdf_urls) == 1:
        reduce_pdf_url = reduce_pdf_urls[0]

        if reduce_pdf_url in pdf_text_cache:
            reduce_text = pdf_text_cache[reduce_pdf_url]
        else:
            reduce_text = extract_text_from_pdf_url(reduce_pdf_url)
            pdf_text_cache[reduce_pdf_url] = reduce_text
            time.sleep(0.3)

        result["股东类型"] = extract_shareholder_type_from_reduce_text(reduce_text)
    else:
        # 如果有多条减持公告链接，不提取股东类型
        result["股东类型"] = ""

    # 4. 找预披露公告
    pre_ann = find_predisclosure_announcement(
        df_ann,
        start_date,
        max_days_before=60
    )

    if pre_ann is not None:
        pre_art_code = pre_ann.get("art_code", "")

        if pre_art_code in content_cache:
            pre_pdf_url = content_cache[pre_art_code]
        else:
            pre_pdf_url = get_pdf_url_from_art_code(pre_art_code)
            content_cache[pre_art_code] = pre_pdf_url
            time.sleep(0.2)

        result["预披露公告链接"] = pre_pdf_url or ""

        # 5. 减持原因：从预披露 PDF 原文中提取
        if pre_pdf_url:
            if pre_pdf_url in pdf_text_cache:
                pre_text = pdf_text_cache[pre_pdf_url]
            else:
                pre_text = extract_text_from_pdf_url(pre_pdf_url)
                pdf_text_cache[pre_pdf_url] = pre_text
                time.sleep(0.3)

            result["减持原因"] = extract_reduce_reason(pre_text)

    return result


def enrich_reduce_result_df(result_df):
    """
    给已有 result_df 补充：
    股东类型、减持原因、减持公告链接、预披露公告链接。
    """

    result_df = result_df.copy()

    # 如果之前已经补充过这些列，先删除，避免重复列
    extra_cols = [
        "股东类型",
        "减持原因",
        "减持公告链接",
        "预披露公告链接"
    ]

    for col in extra_cols:
        if col in result_df.columns:
            result_df = result_df.drop(columns=[col])

    ann_cache = {}
    content_cache = {}
    pdf_text_cache = {}

    extra_rows = []

    for _, row in tqdm(result_df.iterrows(), total=len(result_df)):
        try:
            extra = enrich_one_reduce_row(
                row=row,
                ann_cache=ann_cache,
                content_cache=content_cache,
                pdf_text_cache=pdf_text_cache
            )

        except Exception as e:
            print(
                f"{row.get('股票代码', '')} "
                f"{row.get('公告日期', '')} "
                f"补充信息失败：{e}"
            )

            extra = {
                "股东类型": "",
                "减持原因": "",
                "减持公告链接": "",
                "预披露公告链接": ""
            }

        extra_rows.append(extra)

    extra_df = pd.DataFrame(extra_rows)

    result_df = pd.concat(
        [
            result_df.reset_index(drop=True),
            extra_df.reset_index(drop=True)
        ],
        axis=1
    )

    return result_df

result_df_enriched = enrich_reduce_result_df(result_df)

#print(result_df_enriched.head())
#print(result_df_enriched.shape)
#print(result_df_enriched)

# =========================================================
# 最终结果列名调整 + 重新排序
# =========================================================

final_cols = [
    "股票代码",
    "简称",
    "公告日期",
    "减持股东",
    "股东类型",
    "减持股数",
    "占总股本比例",
    "减持开始日",
    "减持结束日",
    "减持原因",
    "减持公告链接",
    "预披露公告链接"
]

result_df_final = result_df_enriched.copy()

# 只改这两个列名
result_df_final = result_df_final.rename(columns={
    "股票简称": "简称",
    "减持占总股本比例": "占总股本比例"
})

# 按你想要的顺序重新排列，并且丢掉其他不需要的列
result_df_final = result_df_final[final_cols]

#result_df_final.to_excel("result.xlsx", index=False)