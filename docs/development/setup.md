# Hướng dẫn môi trường phát triển

| Mục | Giá trị |
|---|---|
| Trạng thái | Phản ánh code Phase 3 |
| Cập nhật | 2026-11-23 |
| Liên quan | [Kiến trúc §7](../architecture/architecture.md#7-góc-nhìn-triển-khai), [ADR-0010](../adr/0010-runtime-versions.md) |

## 1. Yêu cầu

| Công cụ | Phiên bản đã kiểm tra | Ghi chú |
|---|---|---|
| Docker Desktop (WSL 2) | 29.9.3, Compose v5.7.0 | Chạy Postgres, Redis, Mailpit và toàn bộ stack |
| uv | 0.11 | Tự cài Python 3.14 theo `backend/pyproject.toml` |
| Node.js | 24 | |
| pnpm | 9.15.4 | Ghim qua `packageManager` |

Trên Windows:

- Dùng `127.0.0.1` thay cho `localhost` trong mọi URL (xem ADR-0010).
- Nếu terminal không nhận lệnh `docker`, thêm `C:\Users\<user>\AppData\Local\Programs\DockerDesktop\resources\bin` vào PATH hoặc mở lại terminal.

## 2. Cấu hình lần đầu

```bash
cp .env.example .env                    # mật khẩu dev cho Postgres và hai DB role
cp backend/.env.example backend/.env    # dùng cùng mật khẩu với .env ở gốc
cd backend && uv sync && cd ..
cd frontend && pnpm install && cd ..
cd e2e && pnpm install && pnpm exec playwright install chromium && cd ..
```

Lần đầu container Postgres khởi động, script `infra/docker/postgres/initdb/01-roles.sh` tạo:

- role `viewallet_migrator` (sở hữu database, chạy migration và seed);
- role `viewallet_app` (API và worker; chỉ có quyền DML do migration cấp);
- database `viewallet` (dev) và `viewallet_test` (integration test), cùng extension `pg_trgm`, `citext`.

Script chỉ chạy khi volume `pgdata` còn trống. Muốn chạy lại thì phải xóa volume (`docker compose down -v`). **Lệnh này xóa toàn bộ dữ liệu local.**

## 3. Chạy toàn bộ stack trong Docker

```bash
docker compose up -d --build
bash e2e/wait-for-stack.sh
```

| Dịch vụ | URL |
|---|---|
| App khách hàng | <http://127.0.0.1:8080> |
| App admin | <http://127.0.0.1:8081> |
| API, tài liệu OpenAPI | <http://127.0.0.1:8000/api/docs> |
| Mailpit (email dev) | <http://127.0.0.1:8025> |
| Postgres | `127.0.0.1:55432` |

Service `migrate` chạy `alembic upgrade head` rồi seed dữ liệu tham chiếu (tiền tệ, RBAC, tài khoản ledger hệ thống cho VND, phí và hạn mức mặc định), sau đó thoát với mã 0. Seed không ghi đè rule đã có. `api` và `worker` chỉ khởi động khi `migrate` thành công.

Worker chạy các job định kỳ: `purge_expired_idempotency_keys` (mỗi giờ), `internal_reconciliation` (mỗi giờ, kiểm tra I1–I5), `expire_approval_requests` (mỗi 5 phút, hết hạn sau 72 giờ).

## 3a. Tài khoản nhân viên

Không có đăng ký công khai cho nhân viên. Tạo bằng CLI (mật khẩu đọc từ stdin, không lộ trong lịch sử shell):

```bash
echo "<mật khẩu>" | docker compose exec -T api python -m app.scripts.create_staff \
    --email ops@example.com --name "Ops Demo" --role SUPER_ADMIN
```

Role hợp lệ: SUPPORT, OPERATIONS, FINANCE, RISK_ANALYST, KYC_REVIEWER, ADMIN, SUPER_ADMIN. Lần đăng nhập admin đầu tiên bắt buộc thiết lập TOTP (Google Authenticator, Aegis, 1Password...). Email phải có tên miền thật (ví dụ `example.com`); tên miền dành riêng như `.local` bị từ chối.

Email xác minh, đặt lại mật khẩu và thông báo bảo mật xem tại Mailpit: <http://127.0.0.1:8025>.

## 3b. Tạo số dư demo

Chưa có nạp tiền (Phase 4), nên số dư demo được tạo bằng **điều chỉnh số dư** qua maker-checker, đúng như mọi thay đổi số dư khác:

1. Khách đăng ký, xác minh email, đăng nhập app khách hàng; số ví hiện trên trang Ví.
2. Tạo hai nhân viên role `FINANCE` (mục 3a). Người thứ nhất vào **Điều chỉnh số dư**, chọn lý do "Nạp tiền mô phỏng (demo)", nhập số ví và số tiền.
3. Người thứ hai vào **Phê duyệt** và duyệt. Số dư và lịch sử của khách cập nhật ngay.

Một nhân viên không thể tự duyệt yêu cầu của mình (kiểm tra ở service và bằng CHECK trong DB).

## 4. Phát triển hằng ngày (app chạy trên host)

```bash
docker compose up -d postgres redis mailpit

# backend (terminal 1)
cd backend
uv run alembic upgrade head
uv run python -m app.scripts.seed
uv run uvicorn --factory app.main:app_factory --reload --port 8000

# worker (terminal 2)
cd backend && uv run python -m app.worker

# frontend (terminal 3, 4)
cd frontend && pnpm dev:web      # http://127.0.0.1:5173
cd frontend && pnpm dev:admin    # http://127.0.0.1:5174
```

Vite proxy `/api` và `/health` sang `127.0.0.1:8000`, nên trình duyệt luôn gọi API cùng origin.

## 5. Kiểm tra chất lượng (giống CI)

```bash
# backend
cd backend
uv run ruff format --check . && uv run ruff check .
uv run mypy
uv run lint-imports
uv run pytest --cov --cov-fail-under=90     # integration test cần postgres + redis đang chạy

# frontend
cd frontend
pnpm format:check && pnpm lint && pnpm typecheck && pnpm test && pnpm build

# e2e (cần stack ở mục 3)
cd e2e && pnpm test
```

Integration test đọc `VW_TEST_DATABASE_URL`, `VW_TEST_MIGRATION_DATABASE_URL`, `VW_TEST_REDIS_URL` từ `backend/.env`. Nếu thiếu, pytest bỏ qua các test này và in rõ lý do. Mỗi phiên test dựng lại schema của `viewallet_test` bằng `alembic downgrade base` rồi `upgrade head`, nên cũng kiểm tra luôn chiều downgrade của migration.

## 6. Thay đổi API

Sau khi sửa endpoint hoặc schema Pydantic:

```bash
cd frontend && pnpm gen:api
```

Lệnh này xuất `openapi.json` từ backend và sinh lại `packages/api-client/src/schema.d.ts`. CI báo lỗi nếu một trong hai file lệch với code.

## 7. Thay đổi database

1. Sửa model SQLAlchemy.
2. `cd backend && uv run alembic revision -m "<mô tả>"`, viết migration bằng tay hoặc với `--autogenerate` rồi rà lại.
3. `uv run alembic upgrade head` và `uv run alembic check`. Test `test_models_match_migrations` cũng chạy kiểm tra này.
4. Cập nhật [database.md](../architecture/database.md).

Bảng chỉ-thêm (ledger, event, audit) dùng `make_append_only_sql()` trong `app/core/migration_ops.py`.

## 8. CI/CD

Workflow `.github/workflows/ci.yml` chạy trên mọi pull request và mỗi lần push lên `main`:

| Job | Nội dung |
|---|---|
| `backend` | Postgres 18 + Redis 8 service container, tạo role, ruff, mypy strict, import-linter, pytest + coverage ≥ 90 %, kiểm tra `openapi.json` không lệch |
| `frontend` | Kiểm tra type sinh từ OpenAPI không lệch, prettier, eslint, tsc, vitest, build |
| `e2e` | `docker compose up --build` + Playwright (Chromium, mobile và desktop); khi lỗi thì upload report và log compose |
| `secrets` | gitleaks trên toàn bộ lịch sử git |

Chưa có job triển khai (Phase 13).
