"""把 Workspace / Project 收斂到 tenant 全局根與 Session 根的遷移護欄。

狀態讀取只產生清單，不建立遷移目錄，也不改寫歸屬。catalog 合併、Session 回填、檔案搬移、切換和回滾都要顯式呼叫。
"""
