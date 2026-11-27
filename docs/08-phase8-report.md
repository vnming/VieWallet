# Báo cáo Phase 8 — Admin & Operations

| Mục | Giá trị |
|---|---|
| Trạng thái | Hoàn tất (2026-11-24) |
| Nhánh | `phase-1/foundation` |
| Migration | `0008` |
| Liên quan | [Đặc tả API §3f](api/api-spec.md), [Xử lý tài chính — hoàn tiền](architecture/financial-processing.md), [Hướng dẫn §3b5](development/setup.md) |

## 1. Quyết định của chủ dự án (2026-11-24)

| Câu hỏi | Quyết định |
|---|---|
| Hoàn tiền giá trị lớn (hành trình 9) | Nhân viên đề xuất hoàn tiền cho mọi thanh toán, **luôn** qua maker-checker (Finance duyệt). Merchant tự hoàn vẫn thực hiện ngay như Phase 5 |
| Cấu hình phí, hạn mức | Phiên bản mới qua maker-checker, có hiệu lực khi được duyệt, giữ phiên bản cũ |
| Gán role | Maker-checker; không ai duyệt thay đổi role của chính mình; chỉ Super Admin quyết định role Super Admin; permission của role cố định trong seed |
| Bảng điều khiển simulator | Đi qua backend (khóa admin của simulator nằm ở server), permission riêng, ghi audit, tắt ở production-like |

## 2. Phạm vi đã giao

### Backend

| Phần | Nội dung |
|---|---|
| Hoàn tiền do nhân viên | `staff_refund.py`: đề xuất tạo REFUND `PENDING` (trừ cả đề xuất đang chờ khi tính phần còn hoàn được); duyệt khóa lại thanh toán gốc, kiểm tra lại rồi ghi sổ Nợ ví merchant / Có ví khách; lỗi nghiệp vụ ⇒ `FAILED` với mã lỗi; từ chối/hủy/hết hạn ⇒ `CANCELLED`. Khách không thấy REFUND của nhân viên cho tới khi hoàn tất. State machine thêm REFUND `PENDING → FAILED` |
| Phí, hạn mức có phiên bản | `pricing/versions.py`: bản nháp DRAFT, kích hoạt khi duyệt (phiên bản cũ ⇒ RETIRED, phí có `effective_to`), từ chối ⇒ REJECTED; không cho bản nháp thứ hai; giá trị kiểm tra (bps ≤ 10 000, min ≤ max). DB chỉ cho role ứng dụng UPDATE cột trạng thái/hiệu lực |
| Đổi role | Phê duyệt `ROLE_CHANGE`; kiểm tra ở cả lúc đề xuất và lúc duyệt (người bị đổi không được duyệt, Super Admin chỉ do Super Admin, không gỡ Super Admin cuối cùng); permission nạp lại mỗi request nên có hiệu lực ngay; audit `access.role_granted`/`access.role_revoked` kèm role trước/sau |
| Đối soát provider (FT-28) | `provider_recon.py`: tải sao kê ngày (ngoài DB transaction), so theo operation key; 4 loại mục; dòng khớp không tạo gì; mục có quy trình INVESTIGATING → RESOLVED/WRITTEN_OFF với ghi chú, audit, không di chuyển tiền |
| Console | `app/api/admin_console.py` (lớp HTTP tổng hợp, chỉ dùng `public.py`): dashboard KPI theo quyền, hàng đợi vận hành, sức khỏe hệ thống, bảng điều khiển simulator, đối soát provider |
| Tra cứu khách hàng | `operations/users_api.py`: tìm theo số ví, SĐT, email, tên; hồ sơ, ví, cờ rủi ro, giao dịch gần đây; dữ liệu cá nhân luôn che, xem dữ liệu thật cần `user:pii_reveal:any` + MFA mới và được audit |
| Simulator | `providers/sim_control.py` gọi admin API của simulator bằng khóa ở server; port provider thêm `get_statement` |
| Sửa nhỏ | `reject` kiểm quyền trước khi kiểm `note` (403 thay vì 422 cho người không có quyền); nhân viên không có quyền duyệt nào ⇒ 403 cả khi xem một yêu cầu; giao dịch admin có `refunded`, `related_reference` |

### Frontend (app admin)

Tổng quan (KPI, hàng đợi theo quyền, trạng thái dịch vụ), Khách hàng (tìm, hồ sơ, hiện dữ liệu cá nhân), Hồ sơ giao dịch (lịch sử trạng thái, đề xuất hoàn tiền/đảo), Hàng đợi vận hành, Hỗ trợ (danh sách, hội thoại, ghi chú nội bộ, đổi trạng thái), Phí và hạn mức, Nhân viên và role, Simulator, Sức khỏe hệ thống (`/system`). Trang phê duyệt hiện chi tiết từng loại và **bảng khác biệt trước/sau** cho thay đổi cấu hình; trang đối soát thêm đối soát provider và xử lý mục chênh lệch. Menu chỉ hiện mục người dùng có quyền (backend vẫn kiểm).

## 3. Kết quả kiểm tra (2026-11-24)

| Kiểm tra | Kết quả |
|---|---|
| Backend ruff, mypy strict, import-linter (4 contract), `alembic check`, downgrade/upgrade `0008` | Đạt |
| Backend pytest | **1207 passed**, coverage **93 %** |
| Ma trận authZ: nhân viên không có role gọi **mọi** route admin (lấy từ OpenAPI) ⇒ 403, trừ nhóm route của mọi nhân viên | Đạt (≥ 40 route) |
| Frontend prettier, eslint, tsc, build | Đạt |
| Frontend vitest | **193 passed** (thêm 33 test admin, gồm axe trên 12 trang admin × trạng thái có dữ liệu và lỗi) |
| Playwright trên compose stack: web desktop + mobile, admin, hành trình 9 (hoàn tiền maker-checker) và 10 (sự cố provider) | **34/34** (web 28 với `--workers=2`; admin + sự cố 6 chạy tuần tự) |

Test chính (`test_admin_operations.py`): hành trình 9 (đề xuất idempotent, phần đang chờ được tính, khách không thấy, Operations không duyệt được, Finance duyệt, audit); kiểm tra lại lúc duyệt khi merchant đã tự hoàn; từ chối; chỉ hoàn thanh toán; phí (không đổi ⇒ 409, bản nháp trùng ⇒ 409, ADMIN không duyệt được, Super Admin duyệt ⇒ v2); hạn mức (giá trị sai ⇒ 422, từ chối giữ v1); role (tự đổi, Super Admin, quyền có hiệu lực ngay, cấp trùng ⇒ `ROLE_ALREADY_HELD`, người bị đổi không duyệt được); FT-28 (sao kê lệch 888 ⇒ đúng một mục AMOUNT_MISMATCH, ngày không có giao dịch ⇒ 0); hành trình 10 (OUTAGE ⇒ nạp tiền `PROVIDER_UNAVAILABLE`, sức khỏe DOWN, audit, bảng tắt khi không có khóa); hàng đợi giao dịch treo; dashboard theo quyền; tra cứu che dữ liệu + audit hiện dữ liệu. Unit test `compare()` cho đủ 4 loại chênh lệch.

## 4. Lỗi tìm thấy và đã sửa trong phase

| Lỗi | Phát hiện bởi | Sửa |
|---|---|---|
| Đề xuất phiên bản phí lần hai khi đã có bản nháp gây lỗi 500 (vi phạm index duy nhất) | Test tích hợp | Kiểm tra bản nháp trước, trả 409 `PRICING_RULE_DRAFT_EXISTS` |
| `reject` trả 422 (thiếu `note`) cho người không có quyền thay vì 403 | Ma trận authZ | Kiểm quyền trước, kiểm `note` sau |
| Route SPA `/health` của admin bị nginx chuyển hướng sang api (`/health/`) ⇒ trang 404 JSON | E2E | Đổi thành `/system` |
| Hành trình 10 chuyển simulator sang OUTAGE, làm hỏng test nạp/rút chạy song song | Rà soát E2E | Tách thành project `admin-outage` chạy sau mọi project khác (`dependencies`) |
| Phiên bản phí do test tạo còn lại trong DB test làm downgrade đầu phiên sau dừng (đúng thiết kế) | pytest | Fixture đưa phí/hạn mức về phiên bản seed trước mỗi test và trước downgrade |

## 5. Lệch so với kế hoạch

| Hạng mục | Kế hoạch | Thực tế |
|---|---|---|
| Quản lý user (khóa tài khoản, đổi thông tin khách) | Phase 8 | Chỉ xem (che dữ liệu) + hiện dữ liệu có audit; khóa ví đã có từ Phase 3 |
| Hoạt động admin | Trang riêng | Dùng nhật ký audit có từ Phase 2 (API lọc theo `actor_id`; UI chưa có ô lọc theo nhân viên) |
| Hành trình 10 phần rút tiền UNKNOWN tự giải quyết | E2E | Có test tích hợp từ Phase 4 (truy vấn trạng thái); E2E Phase 8 phủ sự cố, sức khỏe và hàng đợi |
| Gán ticket cho nhân viên, SLA ticket | — | Chưa có (cột `assigned_to` đã có) |
| Cờ tính năng (feature flags), cài đặt chung | Phase 8 | Chưa có: hiện chưa có flag nào cần bật/tắt lúc chạy |
| Xuất audit log | FR-OPS-05 | Chưa có (tìm kiếm có từ Phase 2) |

## 6. Rủi ro còn lại

- Đối soát provider coi thao tác gửi sát nửa đêm (giờ Việt Nam) có thể rơi vào sao kê ngày hôm sau ⇒ có thể sinh mục MISSING_PROVIDER giả; nhân viên đánh dấu RESOLVED.
- Dashboard đếm trực tiếp trên bảng giao dịch; với dữ liệu lớn cần view tổng hợp (Phase 10).
- Máy dev thiếu RAM: build hai app frontend song song bị hết bộ nhớ, phải build lần lượt; E2E admin với 2 worker gặp `net::ERR_INSUFFICIENT_RESOURCES` và docker CLI lỗi giữa chừng, chạy 1 worker thì đạt.

## 7. Bước tiếp theo

Phase 9 — Notifications (Push) & PWA. Cần chủ dự án xác nhận trước khi bắt đầu.
