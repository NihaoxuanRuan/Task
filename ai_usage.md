# AI Usage

## 1. 使用的 AI 工具

使用的 AI 工具是 ChatGPT。

## 2. 关键节点的 prompt 原文

1. 我现在想要先抓取公告日期、减持股东、减持股数、占总股本比例、减持开始日、减持结束日这些数据 我给你以南玻A（代码000012）为例，抓取链接如下 https://datacenter-web.eastmoney.com/api/data/v1/get?sortColumns=END_DATE%2CSECURITY_CODE%2CEITIME&sortTypes=-1%2C-1%2C-1&pageSize=50&pageNumber=1&reportName=RPT_SHARE_HOLDER_INCREASE&quoteColumns=f2~01~SECURITY_CODE~NEWEST_PRICE%2Cf3~01~SECURITY_CODE~CHANGE_RATE_QUOTES&quoteType=0&columns=ALL&source=WEB&client=WEB&filter=(SECURITY_CODE%3D%22000012%22)(DIRECTION%3D%22%E5%87%8F%E6%8C%81%22) 返回结果如图所示，单条数据如下 
{"CHANGE_NUM":3112.11,"NOTICE_DATE":"2022-12-10 00:00:00","SECURITY_CODE":"000012","HOLDER_NAME":"中山润田投资有限公司","AFTER_CHANGE_RATE":1.013488129567,"CHANGE_NUM_SYMBOL":-3112.11,"CHANGE_RATE":-0.1361,"END_DATE":"2022-12-07 00:00:00","CLOSE_PRICE":7.34,"AFTER_HOLDER_NUM":1898.3447,"HOLD_RATIO":0.62,"TRADE_AVERAGE_PRICE":null,"FREE_SHARES_RATIO":0.62,"FREE_SHARES":1898.3447,"TRADE_DATE":"2022-12-07 00:00:00","SECURITY_NAME_ABBR":"南玻A","DIRECTION":"减持","EITIME":"2022-12-09 19:25:06","CHANGE_FREE_RATIO":1.01,"START_DATE":"2022-07-29 00:00:00","REAL_PRICE":null,"NEWEST_PRICE":4.45,"CHANGE_RATE_QUOTES":-1.11,"MARKET":"二级市场"} 
我需要里面的NOTICE_DATE、HOLDER_NAME、CHANGE_NUM_SYMBOL、AFTER_CHANGE_RATE、START_DATE、END_DATE 另外我需要使用HOLD_RATIO + AFTER_CHANGE_RATE来判断是否符合我持股5%以上股东的要求 请你给我相关代码

2. df = ak.index_stock_cons_weight_csindex(symbol="000852") 
pool = df[["成分券代码", "成分券名称"]] 
我使用了以上代码提取了中证1000的股票代码，根据这里面1000个代码，我需要得到中证 1000 成分股最近 3 个月（2026-02-26 至 2026-05-25）"持股 5% 以上股东减持"的信息，也就是说除了变动前持股要大于等于5%这个条件，我还需要额外增加一个筛选公告日期在2026-02-26 至 2026-05-25之间的条件

3. 我现在需要根据上面跑出来的结果，继续提取股东类型、减持原因、减持公告链接和预披露公告链接，在提取过程中如果遇到缺失的直接留空继续，具体流程如下 以000156华数传媒的一条公告日期在2026.4.15，开始日期是2026.1.14，结束日期是2026.4.13为例 首先通过以下链接可以得到图一的结果 
https://np-anotice-stock.eastmoney.com/api/security/ann?sr=-1&page_size=200&page_index=1&ann_type=A&client_source=web&stock_list=000156&f_node=0&s_node=0 
单条数据结构如下 {"art_code":"AN202604141821186970","codes":[{"ann_type":"A,SZA","inner_code":"29241882006902","market_code":"0","short_name":"华数传媒","stock_code":"000156"}],"columns":[{"column_code":"001002007004003","column_name":"股东/实际控制人股份减持"}],"display_time":"2026-04-14 17:39:18:800","eiTime":"2026-04-14 17:40:24:000","language":"0","listing_state":"0","notice_date":"2026-04-15 00:00:00","product_code":"","sort_date":"2026-04-15 12:00:00","source_type":"331","title":"华数传媒:关于持股5%以上的股东减持计划期限届满暨实施情况的公告","title_ch":"华数传媒:关于持股5%以上的股东减持计划期限届满暨实施情况的公告","title_en":""} 
为了找到公告链接，我需要找到notice_date为2026-04-15且title里面含有“减持”二字的数据，提取其中的art_code，通过以下链接，我可以得到图二的内容 
https://np-cnotice-stock.eastmoney.com/api/content/ann?art_code=AN202604141821186970&client_source=web&page_index=1 
其中attach_list里面的attach_url这个参数的值就是我需要的减持公告链接 同理还是在以下这个链接 
https://np-anotice-stock.eastmoney.com/api/security/ann?sr=-1&page_size=200&page_index=1&ann_type=A&client_source=web&stock_list=000156&f_node=0&s_node=0 
我需要找到notice_date在2026.1.14往前最接近2026.1.14且不超过60天的，且title里面含有“预披露”的数据，同样提取其art_code然后通过以下链接 
https://np-cnotice-stock.eastmoney.com/api/content/ann?art_code=AN202604141821186970&client_source=web&page_index=1 
其中attach_list里面的attach_url这个参数的值就是我需要的预披露公告链接 有了这个链接，比如"https://pdf.dfcfw.com/pdf/H2_AN202512091797044225_1.pdf?1765305000000.pdf" 我需要利用类似以下的这个代码 
from curl_cffi import requests
import fitz

def extract_text_from_pdf_url(pdf_url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }

    response = requests.get(
        pdf_url,
        headers=headers,
        impersonate="chrome",
        timeout=20
    )

    print("状态码:", response.status_code)
    print("Content-Type:", response.headers.get("Content-Type"))
    print("返回内容前20字节:", response.content[:20])

    if not response.content.startswith(b"%PDF"):
        print("返回的不是 PDF，前500字符如下：")
        print(response.text[:500])
        return None

    doc = fitz.open(stream=response.content, filetype="pdf")

    text_list = []
    for page in doc:
        text_list.append(page.get_text())

    doc.close()

    return "\n".join(text_list)


pdf_url = "https://pdf.dfcfw.com/pdf/H2_AN202512191803902311_1.pdf?1766174117000.pdf"

text = extract_text_from_pdf_url(pdf_url)

if text:
    print(text[:1000])
else:
print("提取失败")
提取出预披露公告的原文，原文中我想利用类似以下这个函数的方式提取出减持原因 
pattern = r"(?:减持原因|减持股份原因|本次减持的原因|拟减持原因)[:：为]?\s*(.*?)(?=\n|。|；|;|股份来源|减持方式|减持期间|减持数量|$)" 
在预披露公告的开始标题中，如果出现以下字样，关于xxx减持或关于xxx拟减持等字样，这个xxx即为我需要的股东类型

4. 有几个需要修改的地方 
一. 减持原因从冒号后面提取到第一个句号，不要因为换行停下来 
二. 关于减持公告链接的提取，在提取art_code的时候，我需要的是notice_date和公告日期一致以及title含有“减持”或”股东权益“字样的，如果有多条符合的，则这几条的art_code依次使用去提取减持公告链接，全部存入我的数据，中间用逗号或空格分隔 
三. 股东类型改为从减持公告链接提取吧，这意味着减持公告也需要提取pdf内容，关键词除了“关于xxx减持”和“关于xxx拟减持”以外，再添加一个“关于xxx权益”，如果有多条链接，则不要提取股东类型了

5. 我遇到了一个新的问题，你修改完之后还是把新的完整代码给我 就是在搜索公告的时候，page_size我设置的是200，但单个page最多返回100条，所以page_index可以取1和2，换句话说，你需要把page_index的1和2的值都尝试一下

## 3. AI 答案的采用和修改情况

AI 给的答案如果符合我的需求，大体上我不会怎么调整。但对于 runtime 或者个别参数，我会根据实际使用需求来进行调整。

同时，我会对比 AI 给的代码跑出的测试结果和我人工得到的测试结果。如果结果出现偏差，我会回去对代码进行 debug。

## 4. AI 出错情况

### 4.1 参数选择错误

在提取数据的时候，一开始使用了错误的参数。通过测试样本去比对数据后，发现数据存在问题。之后重新使用多个样本去看接口返回数据，结果发现是因为一开始选择了数据值相同的不同参数。



### 4.2 部分公司信息提取为空

AI 给的结果一开始有部分公司信息提取为空。去公告栏核验后，发现公告其实存在且符合提取要求。通过自己手动模拟全过程，最终发现是因为东方财富返回的 page_size 存在大小限制，单次最多返回 100 个数据导致的问题。

## 5. 过去用 AI 做过的项目简介

### 5.1 广发证券实习：IRS 回测系统

**场景：**
市场上现有的 IRS 计算器缺乏统一标准，例如 Wind、Quebee、数据中心等使用的口径各自略有差异，且计算细节不完全透明。因此，希望开发一个自己的 IRS 回测系统，用于统一计算口径，并支持后续回测和预测。

**Prompt 思路：**
我会先在 AI 中创建一个项目，把任务要求给 AI，然后让 AI 给我一个总体上的思路，告诉我大体上要按什么流程来做。对于 AI 提到但我没看懂的术语或背景知识，我会新开一个窗口专门提问。

之后，每个大点都会单独开一个窗口，防止单个窗口对话过长导致 AI 出现幻觉。每个窗口我都会写一个详细的 prompt，解释我在这一部分要实现什么功能。然后我会用测试数据测试 AI 给出的结果是否符合预期。如果有问题，就通过自查代码或继续询问 AI 来找出问题原因，并进一步提出修改 prompt。最终通过一部分一部分地完成各个模块，把整个任务完成。

**最终产出：**
一个可以批量导入导出数据、可以自定义关键参数的 IRS 回测与预测系统。

### 5.2 本科毕业论文：基于机器学习方法的败血症早期检测

**场景：**
医院中关于败血症的检测存在一个问题：病人病情可能会在短时间内突然恶化，从而出现生命危险。因此，希望通过 AI 辅助检测指标，提前半小时预测病人恶化的情况，为临床干预争取更多时间。

**Prompt 思路：**
我先利用 AI 帮我查找相关论文并总结内容，了解这个领域目前常用的一些技术手段。然后让 AI 给我一个总体上的研究思路，分析可以从哪些方面入手来提升预测准确度。

之后，在 AI 的帮助下，我尝试了不同的机器学习模型，例如回归模型、决策树、梯度提升树等。随后又让 AI 帮我梳理构建特征的常见思路。一开始尝试后发现 AUC 指标始终无法达到预期数值，后来通过询问 AI 想到了新的办法，也就是 Blending 模型。通过结合不同模型各自的优点，最终成功实现了预期的预测效果。

**最终产出：**
模型的 AUC 值达到了预期数值，实现了较好的预测效果。
