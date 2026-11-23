# Báo cáo Phase 5 — Transfer, Payment, Refund, Reversal

| Mục | Giá trị |
|---|---|
| Trạng thái | Hoàn tất (2026-11-23) |
| Nhánh | `phase-1/foundation` |
| Migration | `0005` |
| Quyết định của chủ dự án | Merchant tạo bằng CLI `create_merchant` + tài khoản demo (chỉ development/simulation). Hoàn tiền chưa có ngưỡng phê duyệt ở Phase 5. Lỗi nghiệp vụ lúc xác nhận trả 200 + `status = FAILED` như rút tiền. Trả yêu cầu "xin tiền" của người dùng tạo TRANSFER; chỉ yêu cầu của merchant tạo PAYMENT |
| Liên quan | [Xử lý tài chính §13.2](architecture/financial-processing.md#132-phase-5-chuyển-tiền-thanh-toán-hoàn-tiền-đảo-giao-dịch), [Đặc tả API §3c](api/api-spec.md), [Hướng dẫn §3b2](development/setup.md) |

## 1. Phạm vi đã giao

### Backend

| Module | Nội dung |
|---|---|
| `wallet` | Tra cứu người nhận theo số ví/email, trả tên đã che (`NGUYEN V*** A`), rate limit chống dò tìm. Người nhận đã lưu (tối đa 50). QR nhận tiền cá nhân. Ví loại MERCHANT |
| `payments` (mới) | Merchant (ví MERCHANT riêng, role MERCHANT), payment request (QR dùng một lần / dùng nhiều lần / xin tiền đích danh), mã 16 ký tự ngẫu nhiên, chữ ký HMAC kiểm tra mỗi lần đọc và trả, hủy/từ chối, job hết hạn |
| `transactions` | Báo giá và xác nhận chuyển tiền; trả payment request; pipeline xác nhận chung (khóa có thứ tự, trạng thái ví hai bên, hạn mức, trần số dư người nhận, rủi ro, journal trực tiếp hoặc giữ tiền); hủy; hết hạn báo giá; hoàn tiền một phần/toàn phần; đảo giao dịch ròng qua maker-checker; duyệt/từ chối/tự hủy sau 72 giờ cho khoản giữ do rủi ro (chuyển, thanh toán, và rút tiền từ Phase 4) |
| `ledger` | Loại journal `PAYMENT_HOLD`, `PAYMENT_RELEASE_TO_MERCHANT`, `PAYMENT_RELEASE_TO_PAYER`; `net_reversal`/`reverse_transaction` đảo hiệu ứng ròng của nhiều journal |
| `operations` | Action `REVERSAL` trong approvals; API đề xuất đảo; API khoản giữ rủi ro |
| `notifications` | Template theo chiều (gửi/nhận) và loại con: chuyển, nhận, giữ, hủy sau kiểm tra, thanh toán, cửa hàng nhận tiền, hoàn tiền, đảo |
| `core` | `IdempotencyStore.execute_checked`: bước kiểm tra trước transaction tiền (PIN), chạy lại unit of work khi deadlock/serialization |
| CLI | `create_merchant` (có `--create-user`), `create_customer`; chỉ development/simulation, có audit |

### Frontend (app khách hàng)

Chuyển tiền (số ví / email / người nhận đã lưu, kiểm tra tên trước khi chuyển, cảnh báo người nhận mới và chuyển trùng, xem phí, PIN, kết quả có timeline và trạng thái chờ kiểm tra), Quét QR (camera qua Barcode Detection API, nhập mã/dán liên kết; chỉ mở liên kết VieWallet), Thanh toán (tên cửa hàng đã xác minh, số tiền cố định hoặc tự nhập, mã đã đóng), Nhận tiền (QR cá nhân), Xin tiền (tạo yêu cầu có QR, yêu cầu gửi tới tôi, trả/từ chối/hủy), Cửa hàng (số dư ví cửa hàng, tạo QR, danh sách thanh toán, hộp thoại hoàn tiền). QR vẽ tại máy bằng thư viện `qrcode` (MIT), không gửi payload cho dịch vụ ngoài. Menu thêm Chuyển tiền, Quét QR, Cửa hàng (chỉ hiện với merchant).

## 2. Kết quả kiểm tra (2026-11-23)

| Kiểm tra | Kết quả |
|---|---|
| Backend ruff, mypy strict (187 file), import-linter (4 contract; thêm lớp `payments` giữa `transactions` và `wallet`) | Đạt |
| `alembic check`, downgrade/upgrade `0005` (cả khi đã có journal loại mới) | Đạt |
| Backend pytest | **1146 passed**, coverage **93 %** |
| Frontend prettier, eslint, tsc, build | Đạt |
| Frontend vitest | **95 passed** |
| Playwright trên compose stack | **26 passed**, chạy hai lần liên tiếp đều đạt |
| Đối soát I1–I5 trên DB dev sau E2E | 0 vi phạm |

| Mã | Test |
|---|---|
| FT-01 | Số tiền + phí > số dư ⇒ FAILED `INSUFFICIENT_BALANCE`, không journal, số dư không đổi |
| FT-02, FT-03 | 5 lần cùng key: một journal, 4 lần trả body giống hệt + `Idempotent-Replayed`; cùng key khác số tiền ⇒ 422 `IDEMPOTENCY_KEY_REUSED` |
| FT-04 | 10 request cùng key đồng thời: 200 hoặc 409 `IDEMPOTENCY_IN_PROGRESS`, một journal |
| FT-05 | 50 lệnh đồng thời, đủ tiền cho 20: đúng 20 COMPLETED, 30 FAILED, số dư cuối 0 |
| FT-06 | 100 cặp A→B và B→A đồng thời: tất cả COMPLETED, tổng tiền bảo toàn |
| FT-07 | Hai confirm đồng thời: một COMPLETED, một 409 `TXN_INVALID_STATE` |
| FT-08 | Confirm sau 11 phút ⇒ EXPIRED `QUOTE_EXPIRED`; job hết hạn xử lý báo giá còn lại |
| FT-09 | Tự khóa ví sau báo giá ⇒ FAILED `WALLET_FROZEN`, không journal; báo giá mới ⇒ 422 |
| FT-10 | Ví người nhận khóa ⇒ FAILED `RECIPIENT_UNAVAILABLE` (và 422 lúc báo giá) |
| FT-11 | 4 × `PIN_INVALID` rồi `PIN_LOCKED`; PIN đúng vẫn bị từ chối; báo giá giữ CREATED |
| FT-12 | 8 lệnh 1.000.000 ₫ đồng thời, hạn mức ngày 5.000.000 ₫: tổng hoàn tất đúng 5.000.000 ₫ |
| FT-20 | Hoàn 100k ×2 → 150k bị từ chối `REFUND_EXCEEDS_REMAINING` → 100k ⇒ REFUNDED |
| FT-21 | 5 hoàn tiền 100k đồng thời cho thanh toán 300k: đúng 3 thành công |
| FT-22 | Người nhận đã tiêu tiền: REVERSAL FAILED `INSUFFICIENT_BALANCE`, hồ sơ thu hồi, số dư không âm, giao dịch gốc giữ COMPLETED |
| FT-23 | Đảo lần hai ⇒ `TXN_INVALID_STATE` (gốc đã REVERSED) + `UNIQUE reverses_journal_id` |
| FT-27 | Property test mở rộng: chuyển qua review (giữ → duyệt/từ chối) và đảo ròng; I1–I5 sau mọi bước |
| FT-31 | Người nhận gần trần ⇒ FAILED `RECEIVER_LIMIT_EXCEEDED`; đúng bằng trần vẫn được |
| FT-32 | Hai người cùng trả QR dùng một lần: một COMPLETED, một FAILED `PAYMENT_REQUEST_CLOSED` |
| E2E hành trình 4 | Chuyển tiền, người nhận thấy số dư, thông báo, lịch sử và tên người gửi đã che |
| E2E hành trình 5 | Bị chặn do hạn mức, khóa PIN sau 5 lần sai, ví bị khóa |
| E2E hành trình 6 | Merchant tạo QR, khách mở qua trang quét, thanh toán, merchant hoàn một phần, khách thấy tiền hoàn |

Test khác: tra cứu bị rate limit (lần 21 ⇒ 429), không tự chuyển cho mình, ví merchant không nhận chuyển tiền trực tiếp, người nhận đã lưu của người khác ⇒ 404, cảnh báo người nhận mới/chuyển trùng, hủy chỉ khi CREATED; QR để trống số tiền dùng nhiều lần; payment request bị sửa trong DB ⇒ 404; request hết hạn và báo giá không sống lâu hơn request; xin tiền đích danh: người khác không thấy, từ chối, hủy; giữ tiền do rủi ro: duyệt, từ chối, hết hạn 72 giờ, chặn do rủi ro; thanh toán bị giữ rồi từ chối mở lại QR; rút tiền bị giữ: duyệt thì gửi payout, từ chối thì hoàn tiền; đảo bị từ chối; thanh toán đã hoàn một phần không đảo được; maker không thấy yêu cầu nếu không có quyền duyệt.

## 3. Lỗi tìm thấy và đã sửa trong phase

| Lỗi | Phát hiện bởi | Sửa |
|---|---|---|
| Bước xác nhận giữ connection của transaction tiền trong lúc mở thêm connection để kiểm PIN; khi nhiều request đồng thời, pool có thể cạn và treo | Rà soát khi viết FT-05 | Kiểm PIN sau khi claim idempotency và trước khi mở transaction tiền (`execute_checked`) |
| Đảo một lệnh chuyển đã qua review chỉ bằng cách phản chiếu journal cuối sẽ để tiền mắc lại trên HOLD | Thiết kế use case đảo | Đảo hiệu ứng ròng của mọi journal của giao dịch (`net_reversal`), có unit test và property test |
| Downgrade `0005` lỗi khi DB đã có journal loại mới (journal chỉ-thêm, không xóa được) | Fixture test dựng lại schema mỗi phiên | Downgrade đặt lại CHECK cũ với `NOT VALID` |
| Giao dịch nhận tiền hiển thị chính người nhận làm đối tác | Test hai phía của chuyển tiền | Lưu `source_party` cho phía nhận; `customer_view` chọn theo chiều |
| Bộ E2E vượt giới hạn đăng ký (10/giờ/IP) và đăng nhập (20/15 phút/IP) vì mọi người dùng test chung một IP | Chạy toàn bộ E2E | Giữ nguyên giới hạn; khách hàng của hành trình 4–6 tạo qua CLI `create_customer`, bộ đếm rate limit được xóa trước mỗi hành trình Phase 5 |
| Hai bước E2E cũ nhạy thời gian khi máy thiếu RAM (đọc mã TOTP trước khi hiện, chờ chuyển trang 5 giây) | Chạy lặp E2E | Chờ phần tử có nội dung đúng; tăng thời gian chờ chuyển trang |
| E2E maker-checker tìm thẻ phê duyệt theo ghi chú cố định, trúng cả yêu cầu cũ còn lại trong DB dev | Chạy lặp E2E | Ghi chú duy nhất mỗi lần chạy |

Sự cố môi trường: Docker Desktop engine tự khởi động lại khi build song song nhiều image (máy còn dưới 1 GB RAM trống); build lần lượt từng image thì ổn. ESLint toàn repo hết bộ nhớ trong lúc Docker chạy; chạy theo từng thư mục đều sạch. Một lần chạy E2E gặp `ERR_NETWORK_IO_SUSPENDED` do mạng của máy bị tạm dừng.

## 4. Lệch so với kế hoạch

| Hạng mục | Kế hoạch | Thực tế |
|---|---|---|
| Mã lỗi khi xác nhận (FT-01, FT-08, FT-09) | 422 | 200 với `status = FAILED/EXPIRED` + `failure_code` (quyết định của chủ dự án, thống nhất với rút tiền) |
| Ngưỡng phê duyệt hoàn tiền | > 5.000.000 ₫ chờ Finance | Chưa có ở Phase 5 (quyết định của chủ dự án); hoàn tiền do nhân viên đề xuất (`/admin/payments/{ref}/refunds`) chưa làm |
| Đăng ký merchant | — | CLI chỉ development/simulation (quyết định của chủ dự án) |
| Tra cứu theo số điện thoại | `?phone=` | Chưa có cột `users.phone`; tra cứu theo số ví và email |
| Phí merchant (MF) | Cấu hình được | Luôn 0; journal PAYMENT chỉ có phí của người trả (mặc định 0) |
| UI admin cho khoản giữ rủi ro và đảo giao dịch | Phase 8 | Chỉ API (có test); UI ở Phase 8 |
| Quét QR | Camera | Camera qua Barcode Detection API (Chrome/Android); trình duyệt khác dùng nhập mã/dán liên kết. Chưa có thư viện giải mã ảnh dự phòng |

## 5. Rủi ro còn lại

- Ví MERCHANT chưa rút tiền ra ngân hàng và chưa có trần số dư; tiền của cửa hàng chỉ dùng để hoàn tiền.
- Đảo khoản nạp tiền (mô phỏng chargeback) có trong use case nhưng chưa có đối soát với provider.
- Phí của giao dịch gốc được hoàn khi đảo, nhưng giao dịch REVERSAL hiển thị số tiền gốc (phí mặc định 0 nên chưa lộ ra).
- Rate limit tra cứu dựa trên Redis và "fail open" khi Redis không khả dụng (như các rate limit khác).

## 6. Bước tiếp theo

Phase 6 — KYC, Risk & Limits. Cần chủ dự án xác nhận trước khi bắt đầu.
