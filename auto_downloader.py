import os
import sys
import subprocess

# ==================== 自動安裝套件邏輯 ====================
def install_and_import(package, import_name=None):
    if import_name is None:
        import_name = package
    try:
        __import__(import_name)
    except ImportError:
        print(f"【系統提示】偵測到未安裝 {package}，正在自動為您安裝...")
        # 使用目前的 Python 環境執行 pip 安裝
        subprocess.check_call([sys.executable, "-m", "pip", "install", package])
        print(f"【系統提示】{package} 安裝成功！")

# 自動檢查並安裝必備套件
install_and_import("PyGithub", "github")
install_and_import("selenium")
install_and_import("webdriver-manager", "webdriver_manager")
# ========================================================

# 正式進入原本的程式邏輯
import time
from datetime import datetime
from github import Github
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# ==================== 配置設定區 ====================
TARGET_URL = "https://url55.ctfile.com/d/172955-2339886-8818eb?p=197222&d=2339886&fk=16adba"
PASSWORD = "197222"

# 本地暫存下載路徑
TEMP_DOWNLOAD_DIR = os.path.abspath("./temp_download")
os.makedirs(TEMP_DOWNLOAD_DIR, exist_ok=True)

# GitHub 相關設定（請替換成您自己的 Token 與專案資訊）
GITHUB_TOKEN = "您的_GITHUB_PERSONAL_ACCESS_TOKEN"
REPO_NAME = "WaykeYu/TVBox-Checker-"
FILE_PATH_IN_REPO = "data/source.txt"
# ===================================================

# 1. 初始化瀏覽器設定
options = webdriver.ChromeOptions()
prefs = {
    "download.default_directory": TEMP_DOWNLOAD_DIR,
    "download.prompt_for_download": False,
    "directory_upgrade": True
}
options.add_experimental_option("prefs", prefs)

driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
wait = WebDriverWait(driver, 15)

try:
    print("正在開啟網頁...")
    driver.get(TARGET_URL)

    # 2. 輸入密碼
    print("正在嘗試輸入密碼...")
    password_input = wait.until(EC.presence_of_element_located((By.XPATH, "//input[contains(@id, 'pass') or contains(@class, 'pass')]")))
    password_input.clear()
    password_input.send_keys(PASSWORD)
    
    submit_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(@id, 'submit') or contains(@class, 'btn')]")))
    submit_btn.click()
    
    # 3. 檢查日期並尋找今日更新檔案
    today_str = datetime.today().strftime('%Y-%m-%d')
    print(f"密碼解鎖成功，正在檢查是否有 {today_str} 的更新檔案...")
    
    try:
        file_row = wait.until(EC.presence_of_element_located((By.XPATH, f"//*[contains(text(), '{today_str}')]/ancestor::tr | //*[contains(text(), '{today_str}')]/ancestor::div[contains(@class, 'item')]")))
        print("【通知】找到今日更新的檔案！")
        
        download_btn = file_row.find_element(By.XPATH, ".//a[contains(text(), '下載') or contains(@class, 'down')]")
        download_btn.click()
    except Exception:
        print(f"【結束】未找到日期為 {today_str} 的更新檔案，程式結束。")
        driver.quit()
        exit()

    # 4. 等待下載完成並更名
    print("檔案下載中，等待 20 秒...")
    time.sleep(20)

    downloaded_files = [os.path.join(TEMP_DOWNLOAD_DIR, f) for f in os.listdir(TEMP_DOWNLOAD_DIR) if not f.endswith('.crdownload')]
    if not downloaded_files:
        raise Exception("下載失敗，暫存資料夾中沒有找到檔案。")
    
    latest_file = max(downloaded_files, key=os.path.getctime)
    print(f"下載成功，本地暫存路徑: {latest_file}")

    # 5. 使用 GitHub API 上傳檔案
    print("正在透過 GitHub API 上傳檔案...")
    g = Github(GITHUB_TOKEN)
    repo = g.get_repo(REPO_NAME)
    
    with open(latest_file, "r", encoding="utf-8", errors="ignore") as f:
        file_content = f.read()

    try:
        contents = repo.get_contents(FILE_PATH_IN_REPO, ref="main")
        repo.update_file(
            path=FILE_PATH_IN_REPO,
            message=f"Auto-update source.txt ({today_str})",
            content=file_content,
            sha=contents.sha,
            branch="main"
        )
        print("【成功】GitHub 上的 source.txt 已更新！")
    except Exception:
        repo.create_file(
            path=FILE_PATH_IN_REPO,
            message=f"Auto-create source.txt ({today_str})",
            content=file_content,
            branch="main"
        )
        print("【成功】GitHub 上的 source.txt 已成功建立！")

    # 6. 清理本地暫存檔案
    os.remove(latest_file)
    print("本地暫存檔案已清理。")

except Exception as e:
    print(f"🚨 執行過程中發生錯誤: {e}")

finally:
    driver.quit()
    print("瀏覽器已關閉。")
