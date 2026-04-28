import requests
import csv
import time
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

# ========== 配置 ==========
START_PAGE = 1
END_PAGE = 10810                     # 根据实际页数修改
BASE_URL = "https://www.libahao.com/xiaoshuodaquan/page_{}.html"
OUTPUT_FILE = "novel_list.csv"
PROGRESS_FILE = "progress_page.txt"
MAX_WORKERS = 5                       # 并发线程数
REQUEST_DELAY = (0.1, 0.5)            # 随机延时范围（秒）
MAX_RETRIES = 3
RETRY_DELAY = 1

csv_lock = threading.Lock()
progress_lock = threading.Lock()

# ========== 请求会话 ==========
def create_session():
    session = requests.Session()
    retry_strategy = Retry(
        total=2,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })
    return session

# ========== 精准提取书库小说 ==========
def parse_page(page_num, session):
    """返回该页面书库中的小说列表 [(书名, 链接), ...]"""
    url = BASE_URL.format(page_num)
    # 随机延时
    time.sleep(REQUEST_DELAY[0] + (REQUEST_DELAY[1] - REQUEST_DELAY[0]) * (hash(str(page_num)) % 100) / 100)

    try:
        resp = session.get(url, timeout=15)
        if resp.status_code != 200:
            print(f"  ❌ 第 {page_num} 页 HTTP {resp.status_code}")
            return None
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'html.parser')

        # 根据你提供的结构，直接定位书库区域
        # 找到 <div class="booklist"> 下的所有 <li> 中的 <span class="sm"> <a>
        novels = []
        booklist_div = soup.find('div', class_='booklist')
        if not booklist_div:
            # 备用方案：如果页面结构有变化，尝试查找包含“书库”标题的区域
            print(f"  ⚠️ 未找到 div.booklist，尝试备用解析...")
            # 这里可以用之前更宽松的方法，但为了精确，可以跳过该页
            return []

        # 获取所有 <li>，注意第一个 <li> 通常是表头（class="t"），需要跳过
        items = booklist_div.find_all('li')
        for li in items:
            # 跳过表头行（包含 <span class="sm">小说名称</span>）
            if li.find('span', class_='sm', string=lambda s: s and '小说名称' in s):
                continue
            # 在 <span class="sm"> 内找 <a>
            sm_span = li.find('span', class_='sm')
            if not sm_span:
                continue
            a_tag = sm_span.find('a', href=True)
            if not a_tag:
                continue
            title = a_tag.get_text(strip=True)
            href = a_tag['href']
            if not title or not href:
                continue
            # 补全链接
            full_url = href if href.startswith('http') else 'https://www.libahao.com' + href
            novels.append((title, full_url))

        # 去重（避免同一页面重复）
        seen = set()
        unique = []
        for title, url in novels:
            if url not in seen:
                seen.add(url)
                unique.append((title, url))

        print(f"  ✅ 第 {page_num} 页，提取 {len(unique)} 本小说")
        return unique

    except Exception as e:
        print(f"  ⚠️ 第 {page_num} 页解析异常: {e}")
        return None

def fetch_page_with_retry(page_num, session):
    for attempt in range(MAX_RETRIES):
        result = parse_page(page_num, session)
        if result is not None:
            return result
        if attempt < MAX_RETRIES - 1:
            print(f"  🔄 第 {page_num} 页重试 ({attempt+1}/{MAX_RETRIES})...")
            time.sleep(RETRY_DELAY)
    print(f"  ❌ 第 {page_num} 页最终失败，跳过")
    return None

# ========== 进度管理 ==========
def load_progress():
    if not os.path.exists(PROGRESS_FILE):
        return START_PAGE
    with open(PROGRESS_FILE, 'r') as f:
        try:
            return int(f.read().strip())
        except:
            return START_PAGE

def update_progress(page_num):
    with progress_lock:
        current = load_progress()
        if page_num > current:
            with open(PROGRESS_FILE, 'w') as f:
                f.write(str(page_num))

# ========== CSV 写入 ==========
def write_novels_to_csv(novels):
    if not novels:
        return
    with csv_lock:
        with open(OUTPUT_FILE, 'a', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerows(novels)

# ========== 主函数 ==========
def main():
    # 初始化 CSV 表头
    if not os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow(['小说名称', '详情页地址'])

    last_done = load_progress()
    pages_to_do = list(range(last_done, END_PAGE + 1))
    total_pages = len(pages_to_do)
    print(f"📚 已爬取到第 {last_done-1} 页，剩余 {total_pages} 页待处理（共 {END_PAGE} 页）")
    print(f"🚀 使用 {MAX_WORKERS} 个并发线程开始爬取...")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_page = {}
        for page in pages_to_do:
            session = create_session()
            future = executor.submit(fetch_page_with_retry, page, session)
            future_to_page[future] = page

        for future in as_completed(future_to_page):
            page = future_to_page[future]
            try:
                novels = future.result()
                if novels:
                    write_novels_to_csv(novels)
                    update_progress(page)
                else:
                    print(f"  ⚠️ 第 {page} 页未提取到数据，已跳过")
            except Exception as e:
                print(f"  ❌ 第 {page} 页处理异常: {e}")

    print("✅ 所有页面处理完毕！")
    # 可选：删除进度文件
    # if os.path.exists(PROGRESS_FILE):
    #     os.remove(PROGRESS_FILE)

if __name__ == "__main__":
    main()