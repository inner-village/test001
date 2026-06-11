# 引き継ぎメモ: YouTubeトランスクリプト取得

## ゴール
動画 `https://www.youtube.com/watch?v=i65k0u0Mkac` のトランスクリプト全文を取得し、
`transcript_i65k0u0Mkac.txt` として保存する。MCP・APIキーは使わず、CLIとPythonライブラリのみ。

最後に次を報告する: **保存パス / 使用言語 / 手動字幕か自動生成字幕か / 文字数**

## これまでの経緯（重要）
- 前のセッションでは egress ネットワーク許可リストに `youtube.com` 系が無く、
  `www.youtube.com` が `403 "Host not in allowlist"`、`*.googlevideo.com` が到達不可で取得できなかった。
- ユーザーがカスタム許可ドメイン（`youtube.com` / `googlevideo.com` / `googleapis.com`）を追加済み。
- ただしポリシー変更は**起動済みコンテナには反映されない**ため、**この新セッションで初めて有効**になっているはず。

## 新セッションでやること（手順）
1. 到達性チェック:
   ```bash
   curl -sS -o /dev/null -w "%{http_code}\n" https://www.youtube.com
   ```
   - `200` 以外（特に `403 Host not in allowlist`）なら、許可リストがまだ効いていない。
     ユーザーに「許可ドメイン追加後に新セッションを開始したか」を確認する。
2. 依存インストール:
   ```bash
   pip install youtube-transcript-api yt-dlp
   ```
3. 実行:
   ```bash
   python3 fetch_transcript.py
   ```
   - スクリプトが youtube-transcript-api → （失敗時）yt-dlp フォールバックまで自動で行い、
     `transcript_i65k0u0Mkac.txt` を保存し、保存パス/言語/字幕種別/文字数を出力する。
4. 生成された `transcript_i65k0u0Mkac.txt` を確認し、必要ならコミット&プッシュ。

## 取得ロジック（`fetch_transcript.py` に実装済み）
- youtube-transcript-api: 言語フォールバック `ja → ja-JP → en`、**手動字幕を優先**、無ければ自動生成。
- 403/429 等で失敗時は yt-dlp にフォールバック:
  `yt-dlp --skip-download --write-subs --write-auto-subs --sub-langs "ja,ja-JP,en" --sub-format vtt`
  → VTT からタイムスタンプ・インラインタグ・自動字幕の重複行を除去してプレーンテキスト化。

## 追加調査ログ（egress が 200 になった後に判明した壁）
あるセッションで `curl https://www.youtube.com` が **200** になった後も、字幕本体の取得は失敗した。
原因は egress（共有プロキシ）側で、スクリプトのバグではない:

| レイヤ | 結果 |
| --- | --- |
| 到達性 `curl youtube.com` | ✅ HTTP 200 |
| youtube-transcript-api | ❌ `IpBlocked()`（YouTube には到達するが egress IP がブロック） |
| yt-dlp（素のまま） | ❌ `CERTIFICATE_VERIFY_FAILED`（TLS 傍受プロキシのCAが certifi に無い） |
| yt-dlp（`--no-check-certificates`＋ios/androidクライアント） | △ メタデータは取得可。**手動字幕なし／自動字幕 en・ja-orig・ja、元言語=日本語** |
| 字幕本体 timedtext | ❌ HTTP 429（共有 egress IP がレート制限） |

### `fetch_transcript.py` に入れた強化（このブランチ）
- 起動時に system CA (`/etc/ssl/certs/ca-certificates.crt`) を `REQUESTS_CA_BUNDLE`/`SSL_CERT_FILE` に設定。
- yt-dlp に `--no-check-certificates` を付与（プロキシ TLS 傍受対策）。
- `--extractor-args youtube:player_client=ios,tv,web,android` でクライアントを巡回。
- `--sleep-requests 3 --retries 10 --fragment-retries 10` ＋ コマンド全体を指数バックオフ(0/5/15/30s)で再試行（timedtext 429 対策）。
- `--sub-langs ja,ja-orig,ja-JP,en`（元言語の `ja-orig` を追加）。`ja-orig` は自動生成として判定。
- 字幕が取れない限りファイルは生成しない（捏造しない）。

### それでも 429 が続く場合
共有 egress IP の throttle が原因。**新しいセッションを起動して egress IP を変える**のが最も確実。
Full ネットワークのまま「＋ New session」で開き直し、`bash run_fetch.sh` を再実行する。

## ブランチ
`claude/youtube-transcript-extract-og120g`（元メモと `fetch_transcript.py` を含む）
作業ブランチ: `claude/youtube-transcript-fetch-3fhnok`（強化版スクリプトを含む）
