# Báo cáo Phase 4 — Provider Simulator, Deposit & Withdrawal

| Mục | Giá trị |
|---|---|
| Trạng thái | Hoàn tất (2026-11-23) |
| Nhánh | `phase-1/foundation` |
| Migration | `0004` (VieWallet), `s0001` (database riêng của simulator) |
| Quyết định của chủ dự án | Rút tiền cần KYC tier 1: thêm `users.kyc_tier` + CLI `set_kyc_tier` (chỉ development/simulation) tới khi có KYC ở Phase 6. Thời gian chờ tài khoản nhận mới cấu hình được (`VW_NEW_DESTINATION_COOLDOWN_MINUTES`, mặc định 60, E2E đặt 0) |
| Liên quan | [Simulator §8](architecture/payment-provider-simulator.md#8-trạng-thái-cài-đặt-phase-4), [Xử lý tài chính §13.1](architecture/financial-processing.md#131-phase-4-nạp-và-rút-tiền), [Đặc tả API §3b](api/api-spec.md) |

## 1. Phạm vi đã giao

### Payment Provider Simulator (`backend/sim`)

Service FastAPI riêng (cổng 8090), database và role riêng, migration Alembic riêng. API charge/payout/truy vấn/sao kê có Bearer key, idempotent theo `operation_key` (trùng khóa + khác body ⇒ 409, an toàn khi gọi đồng thời). Đủ 10 kịch bản theo số tiền, chế độ NORMAL/DEGRADED/OUTAGE, thao tác tay (ép thành công/thất bại/hết hạn/trả lại, gửi lại callback). Trang SimBank render phía server có CSP chặt và chống open redirect. Worker nội bộ thực hiện hành động hẹn giờ và gửi callback ký HMAC, retry 5 lần.

### VieWallet

| Module | Nội dung |
|---|---|
| `providers` (mới) | Port `PaymentProvider`, adapter HTTP cho simulator (không tự retry, phân biệt "chắc chắn chưa nhận" với "không rõ"), `provider_operations`, hộp nhận callback chỉ-thêm có kiểm chữ ký và khử trùng, endpoint webhook |
| `transactions` | Use case nạp tiền, rút tiền (báo giá → PIN → giữ tiền → gửi sau commit → tất toán/hoàn giữ), hủy, hết hạn báo giá, hoàn tiền khi payout bị trả lại (REVERSAL), áp dụng kết quả provider một chỗ cho cả phản hồi đồng bộ/callback/truy vấn, job truy vấn trạng thái và phục hồi |
| `wallet` | Tài khoản ngân hàng nhận tiền (ngân hàng hư cấu, tối đa 5, số tài khoản mã hóa, phát hiện trùng bằng HMAC) |
| `notifications` | Hộp thư in-app từ sự kiện giao dịch (vi/en, không trùng khi phát lại sự kiện), API danh sách/đếm chưa đọc/đánh dấu đã đọc |
| `operations` | Hồ sơ thu hồi (`recovery_cases`) cho chênh lệch số tiền/trạng thái và thao tác chưa giải quyết sau 72 giờ; API admin xem hồ sơ |
| `risk` | Rule `NEW_DESTINATION_COOLDOWN` (BLOCK) |
| `identity` | `kyc_tier` trong `/me`, CLI `set_kyc_tier` có audit |
| Hạ tầng | Service `simulator` và `sim-migrate` trong compose, script `02-simulator.sh`, CI tạo DB simulator và rút ngắn thời gian chờ khi chạy E2E |

### Frontend (app khách hàng)

Trang Nạp tiền (chọn nhanh số tiền, giải thích kịch bản mô phỏng, chuyển sang SimBank), trang kết quả có polling và timeline, Rút tiền (chặn khi chưa KYC, báo giá phí/tổng/hạn, xác nhận PIN, hủy), Tài khoản nhận tiền, Hộp thư thông báo và chuông đếm chưa đọc trên header, lối tắt Nạp/Rút trên trang Ví. Mỗi ý định gửi tiền dùng một Idempotency-Key; gửi lại sau lỗi mạng dùng lại key đó.

## 2. Kết quả kiểm tra (2026-11-23)

| Kiểm tra | Kết quả |
|---|---|
| Backend ruff, mypy strict (165 file), import-linter (4 contract, thêm contract `app` ⟂ `sim`) | Đạt |
| `alembic check` cho cả hai database, downgrade/upgrade `0004` | Đạt |
| Backend pytest | **1099 passed**, coverage **93,8 %** |
| Frontend prettier, eslint, tsc, build | Đạt |
| Frontend vitest | **82 passed** |
| Playwright trên compose stack (có simulator) | **20 passed**, chạy hai lần liên tiếp đều đạt |
| Đối soát I1–I5 trên DB dev sau E2E | 0 vi phạm |

| Mã | Nội dung | Test |
|---|---|---|
| FT-13 | Callback trùng 3 lần + đồng thời | Kịch bản 555 + 5 callback ký đúng gửi song song: ghi có đúng một lần; các bản trùng ghi `IGNORED_DUPLICATE` |
| FT-14 | Callback sai chữ ký | 401, lưu `REJECTED`, trạng thái không đổi; callback thật sau đó vẫn áp dụng; chữ ký cũ hơn 5 phút bị từ chối |
| FT-15 | Số tiền callback khác operation | Không áp dụng, mở hồ sơ thu hồi `AMOUNT_MISMATCH` |
| FT-16 | `failed` đến sau `succeeded` | Bỏ qua (`IGNORED_STALE`), COMPLETED giữ nguyên |
| FT-17 | Payout timeout ⇒ UNKNOWN ⇒ truy vấn báo thành công | COMPLETED một lần, simulator chỉ có một payout (callback cũng bị làm mất) |
| FT-18 | Payout timeout ⇒ truy vấn báo thất bại | FAILED, hoàn đúng số tiền + phí |
| FT-19 | Sập giữa lúc giữ tiền và lúc gửi | Truy vấn thấy provider chưa nhận ⇒ gửi lại cùng key; sự kiện gửi trùng bị bỏ qua; đúng một payout |
| Mỗi kịch bản simulator | | Integration test cho 111, 222, 333, 444, 555, 666, 777, 888, 999 và OUTAGE; 333/444 kiểm tra qua uvicorn thật (timeout đọc, mất kết nối) |
| E2E hành trình 3 | Nạp thành công qua SimBank, bị từ chối, hoàn tất muộn | `web.money.spec.ts` (mobile + desktop) |
| E2E hành trình 7 | Rút thành công, thất bại được hoàn tiền, timeout được truy vấn giải quyết | `web.money.spec.ts` |

Test khác: tier 0 không rút được; sai PIN giữ báo giá ở CREATED; báo giá hết hạn; thiếu số dư; khóa ví; tài khoản nhận mới bị chặn trong 60 phút; hủy trước khi gửi hoàn giữ, sau khi gửi thì từ chối; provider OUTAGE ⇒ thử lại 3 lần cùng key rồi FAILED và hoàn tiền; payout bị trả lại ⇒ REVERSAL hoàn cả phí; số tài khoản không bao giờ xuất hiện trong response hay trong DB dạng rõ; thông báo không trùng khi phát lại sự kiện; thông báo của người khác ⇒ 404; trang SimBank escape dữ liệu, không chuyển về origin lạ; endpoint điều khiển simulator tắt ngoài môi trường mô phỏng.

## 3. Lỗi tìm thấy và đã sửa trong phase

| Lỗi | Phát hiện bởi | Sửa |
|---|---|---|
| Mọi request tạo charge/payout tới simulator bị 422: alias dependency khai báo trong hàm factory nên FastAPI không resolve được (do `from __future__ import annotations`) và coi là query param | Integration test nạp tiền | Khai báo dependency ở cấp module |
| Provider báo thành công sau khi chính nó đã báo hết hạn bị bỏ qua như callback cũ — có thể làm mất dấu tiền đang nằm ở provider | Test "thành công sau khi hết hạn" | Coi là `STATUS_MISMATCH`, mở hồ sơ thu hồi, không ghi có tự động |
| Connect timeout bị coi là "không rõ kết quả" (ProviderTimeout) | Contract test trên uvicorn thật (Windows trả timeout thay vì từ chối kết nối) | Chưa kết nối được thì chắc chắn chưa nhận ⇒ `ProviderUnavailable` |
| CSP `form-action 'self'` của SimBank chặn việc chuyển về app sau khi duyệt | Rà soát trước khi chạy E2E | Thêm các origin trả về được phép vào `form-action` |
| Test ledger phụ thuộc thứ tự chạy (lấy journal mới nhất theo `posted_at` trong khi test khác tua đồng hồ) | Chạy cả bộ | Sắp theo id (UUIDv7) |

Sự cố môi trường: Docker Desktop engine dừng (API trả 500) giữa lần chạy E2E đầu tiên; khởi động lại Docker Desktop, không liên quan tới code.

## 4. Lệch so với kế hoạch

| Hạng mục | Kế hoạch | Thực tế |
|---|---|---|
| Lưu trữ của simulator | Schema `simulator` | Database riêng `viewallet_sim` + role riêng (cách ly mạnh hơn) |
| Idempotency nạp/rút | Bảng `idempotency_keys` + khóa duy nhất trên `transactions` | Chỉ khóa duy nhất trên `transactions`; gửi lại cùng key trả về trạng thái **hiện tại** thay vì response cũ (cần cho polling và cho luồng có lời gọi provider giữa hai transaction) |
| KYC tier | Phase 6 | Cột `kyc_tier` + CLI (quyết định của chủ dự án) |
| Rút tiền bị risk REVIEW | Analyst duyệt/từ chối | Đã giữ tiền và chờ; API duyệt làm ở Phase 5 |
| Trang "Provider Simulator" trong admin, ghi đè kịch bản theo user, chèn trễ/bỏ/đảo callback | Phase 4 | Điều khiển bằng API có admin key; UI và các điều khiển nâng cao ở Phase 8 |
| Trần số dư khi nạp | Kiểm tra khi ghi có | Kiểm tra lúc tạo lệnh nạp; lúc tất toán tiền đã ở provider nên vẫn ghi có (từ chối sẽ làm lệch sổ) |
| Email cho nạp/rút | — | Chỉ thông báo in-app; push ở Phase 9 |

## 5. Rủi ro còn lại

- Worker xử lý outbox tuần tự: một payout chờ provider (tối đa 10 giây) làm chậm các sự kiện khác. Đủ cho demo; Phase 12 cân nhắc chạy song song theo topic.
- Rút tiền ở trạng thái REVIEW sẽ giữ tiền tới khi có API duyệt ở Phase 5 (chưa có tự hủy sau 72 giờ).
- Bảng `provider_callbacks` và `provider_callback_results` chỉ-thêm nên tăng dần; cần chính sách lưu trữ trước khi chạy dài hạn.
- Docker Desktop trên máy dev (RAM 12 GB) từng dừng khi chạy build + E2E song song; nên tránh chạy kèm Ollama.

## 6. Bước tiếp theo

Phase 5 — Transfer, Payment, Refund, Reversal. Cần chủ dự án xác nhận trước khi bắt đầu.
