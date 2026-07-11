#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
TVBox URL Checker Pro v4
Part 1 - 簡化版 (取消代理解包 + 短網址/代理網址寬鬆驗證)

功能
-------------------------
✓ 讀取 TXT 並保留原格式
✓ 去除重覆網址
✓ 多執行緒高效驗證
✓ 去除空白行、無網址行
✓ 短網址與代理網址：有響應即判定有效 (放寬校驗)
✓ 常規網址 (.json/.m3u8/.txt) 深度內容合規性校驗
"""

from __future__ import annotations
import json
import re
import shutil
import socket
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Set, Dict
from dataclasses import dataclass, field
import requests
import yaml
import urllib3

# 關閉 SSL 未驗證的警告提示
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ============================================================================
# 設定載入
# ============================================================================

try:
    with open("config.yaml", "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
except Exception:
    cfg = {}

INPUT_FILE = cfg.get("input", "data/source.txt")
OUTPUT_FILE = cfg.get("output", "data/source_clean.txt")
INVALID_FILE = cfg.get("invalid", "data/invalid_urls.txt")
DUPLICATE_FILE = cfg.get("duplicate", "data/duplicate_urls.txt")
REPORT_FILE = cfg.get("report", "data/report.md")
MAX_WORKERS = cfg.get("workers", 50)
TIMEOUT = cfg.get("timeout", 8)
RETRY = cfg.get("retry", 3)
BACKUP_ENABLED = cfg.get("backup", True)
HISTORY_DIR = cfg.get("history", "data/history")

USER_AGENT = (
    "Mozilla/5.0 "
    "(Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 "
    "(KHTML, like Gecko) "
    "Chrome/119.0.0.0 "
    "Safari/537.36"
)

# ============================================================================
# 常數與資料結構
# ============================================================================

URL_PATTERN = re.compile(r"https?://[^\s<>\"']+")

# 常見短網址域名特徵 (可根據需求自行增減)
SHORT_URL_DOMAINS = {
    "t.cn", "url.cn", "suo.yt", "suo.im", "dwz.cn", "bit.ly", "tinyurl.com", 
    "git.io", "cutt.ly", "shorturl.at", "rebrand.ly", "t.ly", "is.gd"
}

# 代理網址特徵 (包含常見代理解析網址或特徵關鍵字)
PROXY_KEYWORDS = ["scrapeops", "scraperapi", "proxy", "agent", "api?url=", "?url=", "&url="]

# 無效內容關鍵詞 (僅用於常規網址深度校驗)
INVALID_KEYWORDS = [
    "404", "not found", "access denied", "forbidden", "error",
    "502 bad gateway", "503 service", "nginx", "<html"
]

@dataclass
class CheckResult:
    """單一 URL 檢查結果"""
    url: str
    is_valid: bool
    error_message: Optional[str] = None

@dataclass
class LineResult:
    """單行處理結果"""
    original_line: str
    cleaned_line: str
    urls: List[str] = field(default_factory=list)
    valid_urls: List[str] = field(default_factory=list)
    invalid_urls: List[str] = field(default_factory=list)
    duplicate_urls: List[str] = field(default_factory=list)

# ============================================================================
# URL 檢查器主類別
# ============================================================================

class URLChecker:
    """URL 檢查器主類別 - 高效多執行緒版"""
    def __init__(self):
        self.total = 0
        self.valid = 0
        self.invalid = 0
        self.duplicate = 0
        self.empty_lines = 0
        self.no_url_lines = 0
        
        self.seen_urls: Set[str] = set()
        self.invalid_urls: List[str] = []
        self.duplicate_urls: List[str] = []
        self.url_status: Dict[str, bool] = {}
        
        # 建立 Session 與連線池
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=MAX_WORKERS,
            pool_maxsize=MAX_WORKERS,
            max_retries=RETRY
        )
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)
        
        # 初始化共用的全域執行緒池
        self.executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

    def load(self) -> List[str]:
        p = Path(INPUT_FILE)
        if not p.exists():
            raise FileNotFoundError(f"輸入檔案不存在: {INPUT_FILE}")
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        print(f"📂 載入 {len(lines)} 行資料")
        return lines

    def save(self, lines: List[str]) -> None:
        output_path = Path(OUTPUT_FILE)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        if BACKUP_ENABLED and output_path.exists():
            self._backup_file(output_path)
        
        filtered_lines = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                self.empty_lines += 1
                continue
            if not URL_PATTERN.search(line):
                self.no_url_lines += 1
                continue
            filtered_lines.append(line)
        
        output_path.write_text("\n".join(filtered_lines), encoding="utf-8")
        print(f"\n📊 過濾統計：")
        print(f"  - 移除空白行：{self.empty_lines} 行")
        print(f"  - 移除無網址行：{self.no_url_lines} 行")
        print(f"  - 保留有效行：{len(filtered_lines)} 行")
        print(f"  - 輸出檔案：{OUTPUT_FILE}")

    def _backup_file(self, file_path: Path) -> None:
        history_path = Path(HISTORY_DIR)
        history_path.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(file_path, history_path / f"backup_{ts}.txt")

    def save_invalid(self) -> None:
        if self.invalid_urls: 
            Path(INVALID_FILE).write_text("\n".join(self.invalid_urls), encoding="utf-8")

    def save_duplicate(self) -> None:
        if self.duplicate_urls: 
            Path(DUPLICATE_FILE).write_text("\n".join(self.duplicate_urls), encoding="utf-8")

    def extract_urls(self, line: str) -> List[str]:
        return URL_PATTERN.findall(line)

    def is_short_or_proxy_url(self, url: str) -> bool:
        """判斷網址是否為短網址或代理網址"""
        url_lower = url.lower()
        
        # 1. 檢查代理網址關鍵字
        if any(kw in url_lower for kw in PROXY_KEYWORDS):
            return True
            
        # 2. 檢查短網址域名特徵
        try:
            from urllib.parse import urlparse
            domain = urlparse(url).netloc.lower()
            # 移除 port (如果有，例如 localhost:8080 -> localhost)
            domain = domain.split(':')[0]
            if domain in SHORT_URL_DOMAINS:
                return True
        except Exception:
            pass
            
        return False

    def process_url(self, url: str) -> Optional[CheckResult]:
        """處理單個 URL 核心邏輯（多執行緒進入點）"""
        self.total += 1
        
        # 重複網址過濾
        if url in self.seen_urls:
            self.duplicate += 1
            self.duplicate_urls.append(url)
            return CheckResult(url=url, is_valid=False, error_message="重複 URL")
        
        self.seen_urls.add(url)
        
        # 使用快取或發起連線檢查
        is_valid = self.url_status.get(url) if url in self.url_status else self.check_url(url)
        self.url_status[url] = is_valid
        
        if is_valid:
            self.valid += 1
            return CheckResult(url=url, is_valid=True)
        else:
            self.invalid += 1
            self.invalid_urls.append(url)
            return CheckResult(url=url, is_valid=False, error_message="連線失敗或內容無效")

    def check_all(self) -> None:
        """主排程控制"""
        lines = self.load()
        line_results: List[LineResult] = []
        all_tasks = []
        
        print(f"🔍 開始檢查網址有效性 (執行緒數: {MAX_WORKERS})...")
        
        for line_num, line in enumerate(lines, 1):
            urls = self.extract_urls(line)
            if not urls:
                line_results.append(LineResult(original_line=line, cleaned_line=line, urls=[]))
                continue
            
            line_result = LineResult(original_line=line, cleaned_line=line, urls=urls)
            for url in urls:
                future = self.executor.submit(self.process_url, url)
                all_tasks.append((future, url, line_result))
            
            line_results.append(line_result)
            if line_num % 50 == 0:
                print(f"  進度: {line_num}/{len(lines)} 行")
        
        print(f"  ⏳ 等待所有連線響應...")
        processed_urls: Set[str] = set()
        
        for future, url, line_result in all_tasks:
            try:
                result = future.result(timeout=TIMEOUT + 5)
                if result and url not in processed_urls:
                    processed_urls.add(url)
                    
                    if result.is_valid:
                        line_result.valid_urls.append(url)
                    else:
                        line_result.invalid_urls.append(url)
                        line_result.cleaned_line = line_result.cleaned_line.replace(url, "")
            except Exception as e:
                self.invalid += 1
                self.invalid_urls.append(url)
                line_result.invalid_urls.append(url)
                line_result.cleaned_line = line_result.cleaned_line.replace(url, "")
                print(f"  ⚠️ 檢查 URL 失敗: {url[:50]}... - {str(e)}")
        
        print(f"  ✅ 完成所有網路檢查")
        self.executor.shutdown(wait=True)
        
        cleaned_lines = []
        for result in line_results:
            cleaned = re.sub(r'\s+', ' ', result.cleaned_line).strip()
            if cleaned:
                cleaned_lines.append(cleaned)
        
        self.save(cleaned_lines)
        self.save_invalid()
        self.save_duplicate()
        self.generate_report()

    # ========================================================================
    # 網路連線與深度內容校驗
    # ========================================================================

    def check_url(self, url: str) -> bool:
        """網路效能校驗 (HEAD 預檢 + GET 串流拉取)"""
        for attempt in range(RETRY):
            try:
                # 1. 嘗試使用低成本的 HEAD 請求預檢
                try:
                    head_response = self.session.head(url, timeout=TIMEOUT, allow_redirects=True, verify=False)
                    if head_response.status_code < 400:
                        # 【優化重點】如果是短網址或代理網址，且 HEAD 預檢直接成功響應，則判定為有效
                        if self.is_short_or_proxy_url(url):
                            return True
                except Exception:
                    pass
                
                # 2. 正式發起 GET 串流請求
                response = self.session.get(
                    url, timeout=TIMEOUT, allow_redirects=True, stream=True, verify=False,
                    headers={'Accept': '*/*', 'Accept-Encoding': 'gzip, deflate', 'Connection': 'keep-alive'}
                )
                
                if response.status_code >= 400:
                    if attempt < RETRY - 1:
                        time.sleep(0.5 * (attempt + 1))
                        continue
                    return False
                
                # 【關鍵修改】如果該網址為短網址或代理網址，且狀態碼小於 400 (有正常響應)，直接視為有效！
                if self.is_short_or_proxy_url(url):
                    return True
                
                # 3. 常規網址：檢查基本檔案大小
                content_length = response.headers.get('content-length')
                if content_length and int(content_length) < 100:
                    if attempt < RETRY - 1:
                        time.sleep(0.5 * (attempt + 1))
                        continue
                    return False
                
                # 4. 常規網址：安全讀取前 2KB 內容做結構特徵比對
                content = self._read_content(response)
                if self.validate_content(url, content):
                    return True
                
                if attempt < RETRY - 1:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                return False
            except (requests.exceptions.RequestException, socket.error):
                if attempt < RETRY - 1:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                return False
        return False

    def _read_content(self, response: requests.Response, max_size: int = 2048) -> str:
        content = ""
        try:
            for chunk in response.iter_content(chunk_size=512):
                if chunk:
                    try:
                        content += chunk.decode('utf-8', errors='ignore')
                        if len(content) >= max_size: break
                    except Exception:
                        pass
        except Exception:
            pass
        return content

    def validate_content(self, url: str, content: str) -> bool:
        """常規網址的深度內容校驗 (短網址/代理網址不進入此處)"""
        if not content or len(content.strip()) < 10: return False
        url_lower = url.lower()
        if url_lower.endswith('.json'): return self._validate_json(content)
        elif url_lower.endswith('.xml'): return self._validate_xml(content)
        elif url_lower.endswith(('.m3u', '.m3u8')): return self._validate_m3u(content)
        elif url_lower.endswith('.txt'): return self._validate_txt(content)
        return self._validate_common(content)

    def _validate_common(self, content: str) -> bool:
        content_lower = content.lower()
        for keyword in INVALID_KEYWORDS:
            if keyword in content_lower: return False
        if len(content.strip()) < 20: return False
        tvbox_indicators = ['url', 'name', 'title', 'channel', 'group', 'http', 'https', '://', 'm3u8', 'flv']
        return sum(1 for ind in tvbox_indicators if ind in content_lower) >= 2

    def _validate_json(self, content: str) -> bool:
        content = content.strip()
        if not content or len(content) < 20 or '<html' in content.lower(): return False
        try:
            data = json.loads(content)
            if isinstance(data, dict):
                return any(k in data for k in ['urls', 'channels', 'sites', 'apps']) or len(data) >= 2
            return isinstance(data, list) and len(data) > 0
        except json.JSONDecodeError:
            return False

    def _validate_xml(self, content: str) -> bool:
        content_lower = content.lower()
        return any(i in content_lower for i in ['<?xml', '<tv', '<rss', '<channel']) and ('http' in content_lower or 'channel' in content_lower)

    def _validate_m3u(self, content: str) -> bool:
        return '#EXTM3U' in content.upper() and ('#EXTINF:' in content.upper() or 'HTTP' in content.upper())

    def _validate_txt(self, content: str) -> bool:
        content_lower = content.lower()
        if any(k in content_lower for k in ['404', 'forbidden', 'access denied', 'nginx', '<html', 'error']): return False
        return any(URL_PATTERN.search(line) for line in content.splitlines() if line.strip())

    # ========================================================================
    # 報告生成
    # ========================================================================

    def generate_report(self) -> None:
        lines = [
            "# 📊 TVBox URL 檢查報告", "", "## 📈 統計摘要", "",
            "| 項目 | 數量 | 比例 |", "|------|------|------|",
            f"| 總網址數 | {self.total} | 100% |",
            f"| ✅ 有效 | {self.valid} | {(self.valid/self.total*100):.1f}% |" if self.total > 0 else "| ✅ 有效 | 0 | 0% |",
            f"| ❌ 失效 | {self.invalid} | {(self.invalid/self.total*100):.1f}% |" if self.total > 0 else "| ❌ 失效 | 0 | 0% |",
            f"| 🔄 重複 | {self.duplicate} | {(self.duplicate/self.total*100):.1f}% |" if self.total > 0 else "| 🔄 重複 | 0 | 0% |",
            "", "## 🧹 清理統計", "",
            f"- **移除空白行**：{self.empty_lines} 行", f"- **移除無網址行**：{self.no_url_lines} 行", "",
            f"## ✅ 有效網址 ({self.valid})", "", f"有效網址已儲存至：`{OUTPUT_FILE}`", ""
        ]
        
        lines.extend(["## ❌ 無效網址列表", ""])
        if self.invalid_urls:
            for url in self.invalid_urls[:30]: lines.append(f"- `{url}`")
            lines.append(f"完整清單請查看：`{INVALID_FILE}`")
        else:
            lines.append("✅ 沒有無效網址")
            
        lines.extend(["", "## 🔄 重複網址列表", ""])
        if self.duplicate_urls:
            for url in self.duplicate_urls[:30]: lines.append(f"- `{url}`")
            lines.append(f"完整清單請查看：`{DUPLICATE_FILE}`")
        else:
            lines.append("✅ 沒有重複網址")
            
        lines.extend(["", "---", f"🕐 更新時間：{time.strftime('%Y-%m-%d %H:%M:%S')}", "", "✅ 報告由 TVBox URL Checker Pro v4 自動生成"])
        Path(REPORT_FILE).write_text("\n".join(lines), encoding="utf-8")
        print(f"📄 報告已生成：{REPORT_FILE}")

# ============================================================================
# 主程式進入點
# ============================================================================

if __name__ == "__main__":
    print("=" * 70)
    print("🚀 TVBox URL Checker Pro v4 (寬鬆代理驗證版)")
    print("=" * 70)
    
    start_time = time.time()
    try:
        checker = URLChecker()
        checker.check_all()
        
        print("\n" + "=" * 70)
        print("✅ 檢查完成！")
        print("=" * 70)
        print(f"📊 總網址 : {checker.total}")
        print(f"✅ 有效   : {checker.valid}")
        print(f"❌ 失效   : {checker.invalid}")
        print(f"🔄 重複   : {checker.duplicate}")
        print(f"🧹 移除空白行 : {checker.empty_lines}")
        print(f"🧹 移除無網址行 : {checker.no_url_lines}")
        print(f"⏱️ 耗時   : {time.time() - start_time:.2f} 秒")
        print("=" * 70)
    except Exception as e:
        print(f"💥 程式執行失敗: {e}")
