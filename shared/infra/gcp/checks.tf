# 部署前檢查（check 區塊只在 plan／apply 時顯示警告，不會讓 plan 失敗）。

# image_* 的預設值是 Google 的 hello 示範映像，只為了讓 plan 能成立。
# hello 對任何 POST（含缺欄位、亂碼、空 body）都回 200，Pub/Sub push 會把 200 當成 ack：
# 若照預設值 apply，qsearch-crawl-fetched 的事件會全部被 ack 掉、也不會進 DLQ（Python 驗證 thread 以 docker 實測）。
check "no_placeholder_images" {
  assert {
    condition = alltrue([
      for image in [var.image_api, var.image_worker, var.image_parser, var.image_scheduler, var.image_backfill, var.image_ptt_crawl, var.image_seg, var.image_wordcloud_api, var.image_collector] :
      !startswith(image, "us-docker.pkg.dev/cloudrun/container/")
    ])
    error_message = "仍有 image_* 使用 us-docker.pkg.dev/cloudrun/container/ 的示範映像。正式部署前必須改成 Artifact Registry 的實際映像，否則 parser 會把所有事件 ack 掉。"
  }
}
