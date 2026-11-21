# Báo cáo Phase 1 — Project Foundation

| Mục | Giá trị |
|---|---|
| Trạng thái | Hoàn tất, chờ chủ dự án nghiệm thu |
| Ngày | 2026-11-21 |
| Nhánh | `phase-1/foundation` (chưa commit, chưa push) |
| Liên quan | [Roadmap Phase 1](roadmap.md#phase-1--project-foundation), [Hướng dẫn môi trường](development/setup.md) |

## 1. Phạm vi đã giao

### Backend (`backend/`)

| Thành phần | File | Ghi chú |
|---|---|---|
| Config | `app/core/config.py` | pydantic-settings, prefix `VW_`, không có mật khẩu mặc định |
| Money | `app/core/money.py` | `Money` bất biến, chặn float/bool/Decimal, kiểm tra BIGINT, `basis_points_of` làm tròn half-up bằng số nguyên, `clamp`, chuyển đổi chuỗi API |
| ID, clock | `app/core/ids.py`, `app/core/clock.py` | UUIDv7 (`uuid.uuid7()`), `SystemClock` / `FixedClock` |
| Lỗi | `app/core/errors.py` | RFC 9457 Problem Details, `code` cố định, tiêu đề vi/en theo `Accept-Language`, không lộ chi tiết nội bộ khi lỗi 500 |
| Logging | `app/core/logging.py` | JSON qua structlog, gom cả log uvicorn, che khóa nhạy cảm theo từ (password, pin, token, ...) |
| Middleware | `app/core/middleware.py` | Correlation id (`X-Request-ID` an toàn hoặc tự sinh), access log, security header, `Cache-Control: no-store` |
| DB | `app/core/db.py` | Engine async, `transaction()` (unit of work), naming convention, `run_in_transaction` retry với SQLSTATE 40001/40P01/55P03 |
| Idempotency | `app/core/idempotency.py` | Claim/complete/release đúng thiết kế §7.1, tiếp quản claim bị bỏ dở, tái sử dụng key hết hạn, dependency header `Idempotency-Key` |
| Outbox | `app/core/outbox.py` | `publish` trong transaction của bên gọi, dispatcher `SKIP LOCKED`, lease, backoff lũy thừa, dead-letter, replay |
| Scheduler | `app/core/jobs.py` | Job định kỳ, khóa dòng chống chạy trùng giữa nhiều worker |
| Migration | `migrations/`, `app/core/migration_ops.py` | Baseline `0001`: cấp quyền cho role app, function `app.forbid_mutation()`, bảng `currencies`, `idempotency_keys`, `outbox_events`, `scheduled_jobs`; helper `make_append_only_sql()` |
| API | `app/main.py`, `app/api/health.py` | `/health/live`, `/health/ready` (DB + phiên bản migration + Redis), `/api/v1/system/status`, OpenAPI tại `/api/v1/openapi.json` |
| Worker | `app/worker.py` | Outbox dispatcher + scheduler, dừng an toàn; job `purge_expired_idempotency_keys` |
| Script | `app/scripts/seed.py`, `export_openapi.py` | Seed idempotent (VND bật, USD tắt), xuất OpenAPI |

### Frontend (`frontend/`)

- pnpm workspace gồm `apps/web`, `apps/admin`, `packages/ui`, `packages/shared`, `packages/api-client`.
- `packages/shared`: `formatMoney` dựa trên BigInt (không bao giờ dùng float), i18n (vi mặc định, en), lưu lựa chọn ngôn ngữ.
- `packages/api-client`: type sinh từ OpenAPI, client `openapi-fetch` gửi `Accept-Language`, `ApiError` mang `code` và `correlationId`, hook `useSystemStatus`.
- `packages/ui`: token "Jade Ledger" (sáng/tối, OKLCH), font tự host (Be Vietnam Pro, JetBrains Mono), hỗ trợ reduced motion. Component: Button, TextField, Card, Badge, StatusBadge (đủ 10 trạng thái giao dịch), AmountText, Skeleton, Spinner, EmptyState, ErrorState, Toast, Dialog, SystemStatusPanel.
- `apps/web`: khung app mobile-first (tab dưới, sidebar từ breakpoint `lg`), skip link, chuyển ngôn ngữ, trang chủ hiển thị trạng thái hệ thống thật, trang 404. Style guide `/design` chỉ có trong bản build dev.
- `apps/admin`: khung sidebar, badge môi trường, trang tổng quan sức khỏe dịch vụ, header `X-Robots-Tag: noindex`.

### Hạ tầng

- `compose.yaml` ở gốc repo: postgres 18, redis 8, mailpit, migrate (job một lần), api, worker, web, admin. Mọi cổng chỉ bind `127.0.0.1`.
- `infra/docker/postgres/initdb/01-roles.sh` tạo role `viewallet_migrator` và `viewallet_app`, database dev và test. CI dùng lại chính script này.
- Image backend chạy user không phải root. Frontend dùng `nginx-unprivileged`, có CSP chặt, cache bất biến cho asset có hash.
- `.github/workflows/ci.yml`: backend, frontend, e2e, gitleaks.
- `e2e/`: Playwright (Pixel 7, Desktop Chrome) chạy trên compose stack.

## 2. Kết quả kiểm tra (chạy trên máy dev, 2026-11-21)

| Kiểm tra | Kết quả |
|---|---|
| Backend `ruff format --check`, `ruff check` | Đạt |
| Backend `mypy` (strict, 41 file gồm cả test và migration) | Đạt, 0 lỗi |
| `lint-imports` (2 contract) | 2 kept, 0 broken |
| Backend `pytest` | **165 passed** (unit + integration trên PostgreSQL 18 và Redis thật), coverage **95 %** |
| `alembic check`, vòng downgrade/upgrade | Không lệch giữa model và migration; downgrade về base rồi upgrade lại đều thành công |
| Frontend `prettier --check`, `eslint` (strictTypeChecked, `--max-warnings=0`) | Đạt |
| Frontend `tsc` (5 package) | Đạt |
| Frontend `vitest` | **45 passed** |
| Frontend build (web, admin) | Đạt |
| `docker compose up -d --build` | Mọi service chạy; `/health/ready` = 200; `migrate` thoát 0; worker chạy job thành công |
| Playwright smoke | **11 passed** (web-mobile, web-desktop, admin-desktop) |
| OpenAPI và type TS sinh ra | Đồng bộ với code |

Chưa chạy trên GitHub Actions: workflow sẽ chạy lần đầu khi nhánh được push. Việc push cần anh/chị cho phép.

## 3. Test quan trọng đã có

- Idempotency trên DB thật: replay tuần tự trả cùng response, cùng key nhưng payload khác bị từ chối, 10 request đồng thời chỉ chạy một lần, lỗi nghiệp vụ thì rollback và giải phóng key, tiếp quản claim bị bỏ dở, key hết hạn được dùng lại (FT-02/03/04 ở mức kernel).
- Outbox: publish bị rollback cùng transaction của bên gọi, retry có backoff rồi chuyển DEAD, replay dead-letter, thu hồi lease hết hạn, 4 dispatcher chạy đồng thời không xử lý trùng event nào.
- Scheduler: mỗi chu kỳ chạy đúng một lần, 5 scheduler chạy đồng thời vẫn chỉ chạy job một lần, ghi lại lỗi.
- Bảo vệ ở DB: role app không sửa được dữ liệu tham chiếu, không chạy được DDL; bảng chỉ-thêm chặn UPDATE/DELETE/TRUNCATE kể cả với role sở hữu (SQLSTATE `VW001`), role app không có quyền UPDATE/DELETE trên bảng đó (FT-25 ở mức kernel).
- Money: property test (hypothesis) so sánh làm tròn bps với `Decimal ROUND_HALF_UP`; tính kết hợp và giao hoán của phép cộng.
- HTTP: Problem Details, bản địa hóa, lỗi 500 không lộ chi tiết, correlation id, security header, CORS allow-list, kiểm tra header `Idempotency-Key`.
- Frontend: định dạng tiền chính xác vượt `Number.MAX_SAFE_INTEGER`, a11y của TextField, Dialog, Toast, trạng thái loading/lỗi/thử lại, i18n vi/en có cùng bộ key.

## 4. Lệch so với kế hoạch và quyết định trong Phase 1

| Hạng mục | Kế hoạch | Thực tế | Lý do |
|---|---|---|---|
| Vị trí `compose.yaml` | `infra/compose.yaml` | Ở gốc repo | Để `docker compose up` chạy ngay ở gốc (đúng tiêu chí nghiệm thu) |
| `compose.test.yaml` | Có | Không tạo | Test dùng database `viewallet_test` trên cùng container |
| Dev hot reload | Bind mount trong container | App chạy trên host, chỉ hạ tầng chạy trong Docker | Theo dõi file qua bind mount trên Windows chậm |
| Fixture DB | Savepoint mỗi test | Dựng lại schema mỗi session, TRUNCATE giữa các test | Test đồng thời cần commit thật |
| Factory test | Có nền factory | Dời sang Phase 2 | Chưa có entity nghiệp vụ; tạo sớm chỉ là placeholder |
| Scheduler | Advisory lock | Khóa dòng `FOR UPDATE SKIP LOCKED` | Đơn giản hơn, lưu được trạng thái lần chạy cuối (ADR-0006 đã cập nhật) |
| TypeScript | Bản mới nhất | 6.0 | typescript-eslint chưa hỗ trợ TS 7 |
| React Router | 7 | 8 (bản ổn định hiện tại) | Tài liệu kiến trúc đã cập nhật |
| Cổng Postgres | 5432 | 55432 | Máy đã có PostgreSQL native ở 5432 và 5433 |
| URL dev | `localhost` | `127.0.0.1` | `localhost` → `::1` làm kết nối tới Docker bị treo |
| Storybook | Tùy chọn | Không dùng; thay bằng route `/design` (chỉ có ở bản dev) | Tránh thêm dependency |

## 5. Vấn đề còn lại và rủi ro

- **CI chưa chạy thật**: cần push nhánh. Có thể phải chỉnh nhỏ (ví dụ phiên bản action, thời gian chờ).
- `openapi-typescript` 7.13 khai báo peer `typescript@^5`, nhưng chạy đúng với TS 6. Nếu lỗi thì ghim lại.
- Bundle web khoảng 431 kB (gzip 137 kB). Sẽ tách theo route (lazy loading) khi có nhiều trang (Phase 7).
- Docker Desktop đang dùng khoảng 6 GB RAM. Build web/admin song song từng làm hỏng cache. Đã thêm `sharing=locked`, nhưng nếu lỗi lặp lại thì nên build tuần tự.
- Lớp domain của ledger và transaction chưa có (đúng phạm vi): Phase 3.

## 6. Đề xuất Phase 2 — Identity & Security

Theo [roadmap](roadmap.md#phase-2--identity--security): user và credential (Argon2id), đăng ký và xác minh email qua Mailpit, đăng nhập với JWT access 10 phút cộng refresh token xoay vòng trong cookie httpOnly, session phía server, đăng xuất và đăng xuất mọi phiên, rate limit và khóa tài khoản (Redis), đặt lại/đổi mật khẩu, RBAC (bảng, seed, dependency `require()`), MFA TOTP cho nhân viên, PIN giao dịch (Argon2id + pepper, khóa sau 5 lần sai), module audit có hash chain. UI: đăng ký, đăng nhập, quên/đặt lại mật khẩu, đặt PIN, bảo mật tài khoản, danh sách phiên; đăng nhập admin + MFA.

Cần quyết định trước Phase 2: dùng Ed25519 (EdDSA) hay HS256 để ký JWT. Đề xuất Ed25519: khóa công khai/bí mật tách riêng, dễ xoay khóa.
