# Báo cáo Phase 2 — Identity & Security

| Mục | Giá trị |
|---|---|
| Trạng thái | Hoàn tất (2026-11-22) |
| Nhánh | `phase-1/foundation` (Phase 2 tiếp tục trên cùng nhánh) |
| Quyết định mới | [ADR-0012](adr/0012-server-side-session-cookies.md): session cookie phía server thay cho JWT (ADR-0008 bị thay thế) |
| Liên quan | [Bảo mật](architecture/security.md), [Đặc tả API §3](api/api-spec.md#3-xác-thực-và-định-danh-đã-cài-đặt-ở-phase-2) |

## 1. Phạm vi đã giao

### Backend

| Module | Nội dung |
|---|---|
| `core` | `crypto.py` (token ngẫu nhiên, Argon2id chạy trong thread, AES-256-GCM có key id để xoay khóa), `rate_limit.py` (fixed window trên Redis, fail-open có log), `CsrfProtectionMiddleware`, danh mục tiêu đề lỗi mở rộng theo module |
| `identity` | Đăng ký (không lộ email đã tồn tại), xác minh email bằng mã 6 số (HMAC, tối đa 5 lần thử), đăng nhập web/admin, khóa tài khoản sau 10 lần sai (15 phút, tăng gấp đôi, tối đa 24 giờ), session cookie có hạn nhàn rỗi/tuyệt đối, đăng xuất / đăng xuất mọi phiên / thu hồi từng thiết bị, lịch sử đăng nhập (chỉ-thêm), quên/đặt lại mật khẩu (link 30 phút, thu hồi mọi phiên), đổi mật khẩu, đổi email, PIN giao dịch (Argon2id trên HMAC-pepper, khóa 30 phút sau 5 lần sai, lần khóa thứ 3 trong 24 giờ bắt buộc đặt lại), TOTP cho nhân viên (chống phát lại theo time step, 10 mã khôi phục, step-up 15 phút), CLI `create_staff` |
| `access` | RBAC: 9 role, 28 permission, catalog seed idempotent, `require_permission()` có kiểm tra step-up với permission nhạy cảm; bảng catalog chỉ đọc với role ứng dụng |
| `audit` | Audit log chỉ-thêm, chuỗi SHA-256 ghi dưới advisory lock, che dữ liệu nhạy cảm, API xem (phân trang, lọc) và kiểm tra chuỗi |
| `notifications` | Kênh email qua outbox: phần bí mật (mã, link) được mã hóa trước khi ghi outbox, worker giải mã và gửi qua SMTP (Mailpit); 11 template vi/en |
| Migration `0002` | 13 bảng; `audit_logs` và `login_history` chỉ-thêm (trigger + quyền) |

### Frontend

- **App khách hàng**: đăng ký, xác minh email, đăng nhập (chặn open redirect qua `next`), quên/đặt lại mật khẩu, bắt buộc đặt PIN lần đầu, trang Bảo mật (đổi mật khẩu, đổi/đặt lại PIN, thiết bị đang đăng nhập, lịch sử đăng nhập), đăng xuất xóa toàn bộ cache.
- **App admin**: đăng nhập, thiết lập TOTP (mã QR tạo phía client, secret không lưu trong cache), mã khôi phục hiển thị một lần, xác thực bằng TOTP hoặc mã khôi phục, menu theo permission, trang Nhật ký audit có hộp thoại step-up trước khi kiểm tra chuỗi.
- **Component mới**: `InlineAlert`, `PasswordField`, `PinField`, `buttonClassName`.
- `api-client`: tự gắn header `X-VW-CSRF` cho request ghi, các hàm gọi API identity có type, `fieldErrors`, helper `mockFetch` cho test.

## 2. Kết quả kiểm tra (2026-11-22)

| Kiểm tra | Kết quả |
|---|---|
| Backend ruff, mypy strict (83 file), import-linter | Đạt |
| Backend pytest | **277 passed**, coverage **94,6 %** |
| `alembic check`, downgrade/upgrade `0002` | Đạt |
| Frontend prettier, eslint (`--max-warnings=0`), tsc | Đạt |
| Frontend vitest | **62 passed** |
| Build web/admin, image Docker | Đạt |
| Playwright trên compose stack | **15 passed**, chạy hai lần liên tiếp đều đạt |

Test quan trọng: luồng đăng ký → xác minh → đăng nhập → đăng xuất; email đã tồn tại và sai mật khẩu trả về giống hệt; rate limit đăng nhập (lần thứ 6 ⇒ 429); khóa tài khoản khi tấn công từ nhiều IP; hạn phiên nhàn rỗi và tuyệt đối; thu hồi phiên của người khác ⇒ 404; token đặt lại mật khẩu dùng một lần, hết hạn sau 30 phút; mã xác minh bị hủy sau 5 lần sai; bí mật trong outbox đã được mã hóa; PIN khóa sau 5 lần sai và **bộ đếm được lưu lại cả khi request lỗi** (FT-11); PIN không verify được nếu thiếu pepper; TOTP bị từ chối khi phát lại; mã khôi phục chỉ dùng một lần; phiên MFA chờ hết hạn sau 10 phút; tách audience khách/nhân viên; mọi route `/api/v1/admin` từ chối người ẩn danh và khách hàng (sinh tự động từ OpenAPI); step-up cho permission nhạy cảm; chuỗi audit phát hiện bản ghi bị sửa; 25 lần ghi audit đồng thời vẫn giữ chuỗi tuyến tính.

## 3. Lỗi tìm thấy và đã sửa trong phase

| Lỗi | Nguyên nhân | Sửa |
|---|---|---|
| Health check báo DB lỗi khi nhiều người đăng nhập cùng lúc | Argon2id (64 MB) chạy đồng bộ trên event loop, chặn mọi request | Chạy hash trong thread (`ahash/averify`), có test chứng minh event loop không bị chặn |
| Admin bị đẩy về trang login ngay sau khi xác thực MFA | Cache vẫn giữ kết quả "chưa đăng nhập" cũ | Xóa query khỏi cache thay vì chỉ invalidate |
| Bộ đếm sai mã/sai PIN có thể bị rollback | Ném lỗi bên trong transaction | Hàm kiểm tra trả kết quả; service commit rồi mới ném lỗi |
| Hai nút cùng nhãn "Đăng xuất" | Nút thu hồi thiết bị trùng nhãn nút đăng xuất | Đổi thành "Đăng xuất thiết bị" |
| gitleaks báo nhầm | UUID cố định trong test | Sinh UUID lúc chạy, thêm fingerprint vào `.gitleaksignore` |

## 4. Lệch so với kế hoạch

| Hạng mục | Kế hoạch | Thực tế |
|---|---|---|
| Xác thực | JWT + refresh cookie (ADR-0008) | Session cookie phía server (ADR-0012, chủ dự án quyết định) |
| Danh sách mật khẩu bị lộ | 100k mật khẩu | Danh sách tích hợp khoảng 60 mật khẩu yếu phổ biến cộng luật chống dùng email/tên; thay được bằng file lớn hơn |
| SĐT khi đăng ký | Tùy chọn | Bỏ khỏi đăng ký; thêm vào hồ sơ ở Phase 7 (tránh lộ SĐT đã tồn tại) |
| Link "không phải tôi" khi đổi email | Có | Dời sang Phase 7; email thông báo tới địa chỉ cũ đã có |
| Cache Redis cho tra session | ≤ 30 giây | Tra DB theo index unique mỗi request (đủ nhanh ở quy mô này) |
| Email nhân viên | Bất kỳ | Phải là tên miền hợp lệ (`email-validator` từ chối `.local`) |

## 5. Rủi ro còn lại

- Khách hàng chưa có 2FA (chỉ nhân viên). PIN bảo vệ các thao tác trừ tiền từ Phase 3.
- Rate limit fail-open khi Redis sập; khóa tài khoản và khóa PIN vẫn hoạt động vì lưu trong PostgreSQL.
- Audit ghi dưới một advisory lock toàn cục, nên mọi transaction có ghi audit phải chờ nhau. Đủ dùng ở quy mô hiện tại; cần đo lại khi load test ở Phase 12.

## 6. Bước tiếp theo

Phase 3 — Wallet & Financial Core (chủ dự án đã cho phép).
