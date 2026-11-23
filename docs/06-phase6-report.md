# Báo cáo Phase 6 — KYC, Risk & Limits

| Mục | Giá trị |
|---|---|
| Trạng thái | Hoàn tất (2026-11-23) |
| Nhánh | `phase-1/foundation` |
| Migration | `0006` |
| Quyết định của chủ dự án | Tệp KYC: Pillow mã hóa lại ảnh + volume local, tệp mã hóa khi lưu. Tham số risk rule trong bảng `risk_rules` có phiên bản, đổi qua maker-checker. Giữ CLI `set_kyc_tier` cho development/E2E |
| Liên quan | [Bảo mật §8.4, §9.1](architecture/security.md), [Đặc tả API §3d](api/api-spec.md), [Hướng dẫn §3b3](development/setup.md) |

## 1. Phạm vi đã giao

### Backend

| Module | Nội dung |
|---|---|
| `kyc` (mới) | Hồ sơ, bản nháp, tải ảnh (mặt trước, mặt sau, chân dung), gửi, hàng đợi reviewer, nhận hồ sơ, duyệt (tier 1, 730 ngày) / từ chối có mã lý do, nộp lại (tối đa 5 lần/30 ngày), hết hạn tự động. Số giấy tờ mã hóa + HMAC phát hiện trùng. Tệp được kiểm tra và làm sạch (Pillow), mã hóa AES-GCM trên đĩa, chỉ xem qua link ký 5 phút gắn với đúng nhân viên, mỗi lần xem có audit |
| `risk` | Đủ 11 rule của security.md §8.2, mỗi rule đọc tham số từ cấu hình có phiên bản và trả về bằng chứng. Bảng `risk_rules` (một phiên bản ACTIVE mỗi rule), cờ tài khoản, cảnh báo rủi ro |
| `transactions` | Lắp ngữ cảnh rủi ro dùng chung cho chuyển, thanh toán, rút tiền (dữ kiện chậm đọc trước khi khóa ví). Giám sát bất đồng bộ: FAN_IN, ROUND_TRIP, REPEATED_PIN_LOCK. API `GET /wallet/limits` |
| `operations` | Action `RISK_RULE_CHANGE` trong approvals; API cảnh báo, cờ, phiên bản rule |
| `identity` | `set_tier_for_user` là nơi duy nhất ghi `kyc_tier`; sự kiện `identity.pin_locked` |
| `notifications` | Thông báo KYC: đã nhận, đã duyệt, bị từ chối (kèm lý do), hết hạn |
| `access` | Quyền `risk_rule:propose:any`, `risk_rule:approve:any` (RISK_ANALYST) |
| Hạ tầng | Volume `kycdata` cho API, thư mục trong image thuộc user `app` |

### Frontend

- App khách hàng: trang Xác minh (thông tin hư cấu, tải 3 ảnh, gửi, trạng thái chờ/đang duyệt/đã duyệt/bị từ chối kèm lý do/hết hạn, cảnh báo "không tải giấy tờ thật"), trang Hạn mức (tối đa, đã dùng, còn lại theo loại và kỳ, trần số dư, liên kết nâng cấp), liên kết từ trang Rút tiền và Ví.
- App admin (màn hình tối thiểu theo roadmap): Duyệt KYC (hàng đợi, xem ảnh qua link ký, cảnh báo trùng giấy tờ, nhận hồ sơ, duyệt/từ chối có lý do), Rủi ro (khoản giữ chờ quyết định có step-up MFA, cảnh báo đang mở). Menu Phê duyệt hiện cho người có quyền duyệt đảo giao dịch và rule rủi ro.

## 2. Kết quả kiểm tra (2026-11-23)

| Kiểm tra | Kết quả |
|---|---|
| Backend ruff, mypy strict (201 file), import-linter (4 contract; `kyc` cùng lớp với `wallet`) | Đạt |
| `alembic check`, downgrade/upgrade `0006` | Đạt |
| Backend pytest | **1185 passed**, coverage **93 %** |
| Frontend prettier, eslint, tsc, build | Đạt |
| Frontend vitest | **106 passed** |
| Playwright trên compose stack (`--workers=2`) | **28 passed**, chạy hai lần liên tiếp đều đạt |
| Đối soát I1–I5 trên DB dev sau E2E | 0 vi phạm |

| Nghiệm thu | Test |
|---|---|
| Vòng đời KYC | `test_kyc.py`: bản nháp sai (dưới 18 tuổi), thiếu ảnh, gửi, không sửa được khi chờ, khách không vào được API reviewer, phải nhận trước khi quyết định, reviewer khác không nhận/duyệt được, link xem ảnh: đúng người, sai chữ ký, hết hạn, có audit; từ chối cần lý do; nộp lại rồi duyệt ⇒ tier 1 ở `/me`, `/kyc`, hạn mức; thông báo; trùng số giấy tờ ⇒ cờ `DUPLICATE_DOCUMENT`; lần nộp thứ 6 trong 30 ngày ⇒ 429; hết hạn sau 730 ngày ⇒ tier 0; ảnh giả, tệp > 5 MB |
| Từng risk rule kèm bằng chứng | `test_phase6_units.py`: mỗi rule một test hai phía ngưỡng (khớp ⇒ đúng bằng chứng; sát ngưỡng ⇒ không khớp), rút tiền ⇒ BLOCK với rule đổi thông tin đăng nhập, giờ địa phương cho NIGHT_LARGE, tổng điểm ⇒ REVIEW; phiên bản rule đổi tham số/tắt rule/dấu vân tay; cấu hình sai bị từ chối |
| Tệp KYC | Xóa EXIF và dữ liệu nối đuôi, thu nhỏ ảnh lớn, từ chối GIF/JPEG giả/PDF có JavaScript hoặc OpenAction/PDF hỏng, mã hóa khi lưu, phát hiện tệp bị sửa, chặn khóa lưu trữ thoát thư mục |
| Rule qua maker-checker | `test_risk_admin.py`: tham số sai ⇒ 422, đề xuất trùng ⇒ 409, tự duyệt ⇒ 403, người thứ hai duyệt ⇒ phiên bản mới ACTIVE, cũ RETIRED, giao dịch tiếp theo bị đánh giá theo phiên bản mới |
| Cờ, cảnh báo, hạn mức | WATCHLIST cho bằng chứng trong assessment, gỡ cờ cần lý do; FAN_IN ở người gửi thứ 5, ROUND_TRIP chỉ cảnh báo chiều về, phát lại sự kiện không tạo cảnh báo mới; khóa PIN lần 2 ⇒ REPEATED_PIN_LOCK; hạn mức còn lại đúng sau một lệnh chuyển |
| E2E hành trình 2 | `admin.kyc-risk.spec.ts`: khách nộp KYC qua UI (3 ảnh), reviewer nhận và duyệt ở app admin, khách thấy cấp 1 và thông báo |
| E2E hành trình 8 | Tài khoản mới, vừa đặt PIN, chuyển 6.000.000 ₫ lần đầu ⇒ giữ tiền; analyst duyệt ở app admin; trang kết quả của khách tự chuyển sang "Chuyển tiền thành công"; người nhận thấy tiền |

## 3. Lỗi tìm thấy và đã sửa trong phase

| Lỗi | Phát hiện bởi | Sửa |
|---|---|---|
| Ngữ cảnh rủi ro mới (trung vị 90 ngày, cờ, hạn mức…) được đọc trong lúc giữ khóa ví ⇒ thời gian giữ khóa dài ra; dưới tải (FT-06 khi chạy cùng coverage) vượt `lock_timeout` | Chạy toàn bộ test có coverage | Tách `gather_facts` (trước khi khóa) và `build_context` (chỉ đếm vận tốc trong vùng khóa) |
| ROUND_TRIP cảnh báo cả chiều đi khi sự kiện được xử lý sau khi chiều về đã hoàn tất | Test cảnh báo | Chỉ so với chiều trước đó (thời điểm, rồi id UUIDv7 khi trùng thời điểm) |
| Lỗi dòng nối trong Dockerfile làm build image backend thất bại âm thầm (vòng lặp build chỉ in dòng cuối) | Migrate container báo thiếu revision `0006` | Sửa Dockerfile; build từng image và kiểm tra lỗi |
| Migration tự sinh không thấy bảng KYC vì `migrations/env.py` không import module mới | Autogenerate | Import tường minh `kyc`, `payments`, `wallet.recipients` |
| Test tua đồng hồ làm phiên đăng nhập hết hạn; một test để lại khoản giữ làm hỏng bất biến I5 ở test sau | Chạy test | Kiểm tra trực tiếp DB sau khi tua; mọi test tự giải quyết khoản giữ của mình |
| E2E hành trình 2 chọn hồ sơ theo tên cố định, trúng hồ sơ cũ còn trong DB dev từ lần chạy dở | Chạy lặp E2E, đọc trace | Tên hồ sơ ngẫu nhiên mỗi lần chạy |

Sự cố môi trường: máy còn 0,4–1 GB RAM trống khi Docker, trình duyệt và test chạy cùng lúc. Hệ quả: tiến trình Playwright crash (`0xC0000409`), đăng nhập chậm tới 5 giây làm E2E 4 worker thất bại hàng loạt, vitest crash (mã 134). Chạy E2E với `--workers=2` và vitest với `--maxWorkers=2` thì ổn định; không phải lỗi code.

## 4. Lệch so với kế hoạch

| Hạng mục | Kế hoạch | Thực tế |
|---|---|---|
| Upload giấy tờ | multipart | Body là tệp thô (không cần thư viện multipart), stream có giới hạn 5 MB |
| Kiểm tra tự động mô phỏng | Tùy chọn | Chưa làm; reviewer quyết định bằng tay |
| Signed URL | URL ngắn hạn | Link ký HMAC 5 phút, gắn với nhân viên được cấp, vẫn cần phiên admin và quyền |
| UI admin cờ tài khoản, rule rủi ro, xử lý cảnh báo | Phase 8 | Chỉ API (có test); UI admin Phase 6 gồm Duyệt KYC và Rủi ro (khoản giữ, danh sách cảnh báo) |
| Seed ảnh giấy tờ mẫu | Có | Không cần: E2E và test tự tạo ảnh PNG hư cấu |
| Hạn mức theo tier | Phase 6 | Bảng hạn mức có từ Phase 3; Phase 6 thêm API hạn mức còn lại và luồng lên tier thật |

## 5. Rủi ro còn lại

- Tệp KYC bị thay thế trong bản nháp vẫn nằm trên đĩa (đã mã hóa, không còn được tham chiếu); cần job dọn dẹp.
- Cảnh báo REPEATED_PIN_LOCK dựa trên bộ đếm khóa trong cửa sổ 24 giờ của PIN.
- Rule mặc định trong code và phiên bản 1 trong bảng phải giữ khớp khi thêm rule mới (seed idempotent không ghi đè phiên bản đã có).
- Máy dev 12 GB RAM: Docker + build + Playwright cùng lúc gây crash ngẫu nhiên; build từng image.

## 6. Bước tiếp theo

Phase 7 — Hoàn thiện app khách hàng. Cần chủ dự án xác nhận trước khi bắt đầu.
