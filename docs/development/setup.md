# Hướng dẫn môi trường phát triển

| Mục | Giá trị |
|---|---|
| Trạng thái | Phản ánh code Phase 4 |
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

Script `02-simulator.sh` tạo role `viewallet_sim` và database `viewallet_sim`, `viewallet_sim_test` cho Payment Provider Simulator (ADR-0005). Volume đã có từ trước Phase 4 thì chạy tay một lần (không xóa dữ liệu):

```bash
docker compose cp infra/docker/postgres/initdb/02-simulator.sh postgres:/tmp/02-simulator.sh
docker compose exec -e VW_DB_SIM_PASSWORD=<giá trị trong .env> postgres bash /tmp/02-simulator.sh
```

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
| Payment Provider Simulator (SimBank) | <http://127.0.0.1:8090/health> |
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

## 3b. Nạp, rút tiền mô phỏng

- **Nạp tiền**: app khách hàng → Nạp tiền → trang SimBank (`127.0.0.1:8090`) → Duyệt. Số tiền quyết định kịch bản: `…111.000 ₫` thất bại, `…222.000 ₫` hoàn tất sau 2 phút, `…333.000 ₫` phản hồi chậm (timeout rồi truy vấn trạng thái), `…555.000 ₫` callback trùng, `…666.000 ₫` callback đầu sai chữ ký, `…777.000 ₫` ngân hàng không khả dụng, `…999.000 ₫` (rút) bị trả lại sau 5 phút. Danh sách đủ ở [simulator §4](../architecture/payment-provider-simulator.md#4-kịch-bản).
- **Rút tiền** cần KYC tier 1. Trước Phase 6 nâng tier bằng CLI (chỉ development/simulation, có audit):

  ```bash
  docker compose exec -T api python -m app.scripts.set_kyc_tier --email lan@example.com --tier 1
  ```

  Tài khoản nhận mới phải chờ 60 phút (`VW_NEW_DESTINATION_COOLDOWN_MINUTES`).
- **Điều khiển simulator** (header `X-Sim-Admin-Key: $VW_SIMULATOR_ADMIN_KEY`): `PUT /v1/admin/settings {"mode": "OUTAGE"}`, `GET /v1/admin/operations`, `POST /v1/admin/operations/{key}/actions {"action": "SUCCEED"}`.

Worker chạy thêm: `provider_status_inquiries` (10 giây), `expire_withdrawal_quotes` (1 phút), `resubmit_stalled_deposits` (1 phút).

## 3b2. Chuyển tiền, thanh toán QR, cửa hàng mô phỏng (Phase 5)

- **Chuyển tiền**: app khách hàng → Chuyển tiền → nhập số ví hoặc email → Kiểm tra (hiện tên đã che, ví dụ `NGUYEN V*** A`) → số tiền → xem phí → PIN. Tra cứu người nhận giới hạn 20 lần/10 phút và 100 lần/ngày mỗi user.
- **Cửa hàng mô phỏng** (chỉ development/simulation, có audit). Biến một khách hàng có sẵn thành cửa hàng, hoặc tạo luôn tài khoản demo (mật khẩu đọc từ stdin):

  ```bash
  docker compose exec -T api python -m app.scripts.create_merchant --email lan@example.com --name "Quán Lan"
  echo "<mật khẩu>" | docker compose exec -T api python -m app.scripts.create_merchant --create-user       --email cafe-may@example.com --owner "Lê Thu Mây" --name "Cà phê Mây (mô phỏng)"
  ```

  Cửa hàng có ví MERCHANT riêng; trang `/merchant` tạo QR (dùng một lần hoặc dán tại quầy), xem thanh toán, hoàn tiền.
- **Khách hàng demo không qua đăng ký** (dùng cho E2E vì đăng ký bị giới hạn theo IP):
  `echo "<mật khẩu>" | docker compose exec -T api python -m app.scripts.create_customer --email an@example.com --name "Nguyễn Văn An"`.
- **Quét QR**: trang `/scan` dùng camera khi trình duyệt có Barcode Detection API (Chrome/Android); nơi khác nhập mã 16 ký tự hoặc dán liên kết `/pay/<mã>`.
- **Giữ tiền do rủi ro**: nhân viên RISK_ANALYST (MFA còn hiệu lực) xem `GET /api/v1/admin/risk/holds`, duyệt/từ chối `POST .../{ref}/approve|reject`. UI admin ở Phase 8.
- **Đảo giao dịch**: OPERATIONS/FINANCE đề xuất `POST /api/v1/admin/transactions/{ref}/reversals`, người thứ hai có `reversal:approve:any` duyệt qua `/api/v1/admin/approvals`.

Worker chạy thêm: `expire_spend_quotes` (1 phút), `expire_payment_requests` (5 phút), `expire_risk_holds` (15 phút, hủy khoản giữ quá 72 giờ).

## 3b3. KYC, hạn mức, rủi ro (Phase 6)

- **KYC**: app khách hàng → Xác minh → nhập thông tin hư cấu → tải ảnh giả lập (mặt trước, mặt sau, chân dung) → Gửi hồ sơ. Nhân viên `KYC_REVIEWER` duyệt ở app admin → Duyệt KYC (nhận hồ sơ → duyệt/từ chối). Tệp nằm trên volume `kycdata` (đã mã hóa); chạy ngoài Docker thì ở `backend/var/kyc` (`VW_KYC_STORAGE_DIR`).
- CLI `set_kyc_tier` vẫn dùng được ở development/simulation (E2E rút tiền), giờ cập nhật luôn hồ sơ KYC như một lần duyệt mô phỏng.
- **Hạn mức còn lại**: Ví → Hạn mức (`GET /api/v1/wallet/limits`).
- **Bàn rủi ro**: nhân viên `RISK_ANALYST` → Rủi ro: khoản giữ chờ quyết định (duyệt/từ chối) và cảnh báo. Đổi tham số rule: `POST /api/v1/admin/risk/rules/{code}/versions`, người thứ hai duyệt ở Phê duyệt. Rule mặc định được seed (`python -m app.scripts.seed`).

Worker chạy thêm: `expire_kyc_approvals` (1 giờ). Handler mới: giám sát rủi ro trên `transaction.status_changed` và `identity.pin_locked`; thông báo KYC trên `kyc.status_changed`.

## 3c. Tạo số dư demo

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

# provider simulator (terminal 2b)
cd backend
uv run alembic -c sim_alembic.ini upgrade head
uv run uvicorn --factory sim.main:app_factory --port 8090

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

# e2e: dựng stack với thời gian chờ rút ngắn như CI, rồi chạy Playwright
VW_NEW_DESTINATION_COOLDOWN_MINUTES=0 SIM_PENDING_DELAY_SECONDS=5 SIM_SLOW_RESPONSE_SECONDS=15   docker compose up -d --build
cd e2e && pnpm test
```

Integration test đọc `VW_TEST_DATABASE_URL`, `VW_TEST_MIGRATION_DATABASE_URL`, `VW_TEST_REDIS_URL`, `VW_TEST_SIM_DATABASE_URL` từ `backend/.env`. Simulator chạy thật trong test (ASGI, cùng process); một test khác dựng uvicorn thật để kiểm tra timeout và mất kết nối. Nếu thiếu, pytest bỏ qua các test này và in rõ lý do. Mỗi phiên test dựng lại schema của `viewallet_test` bằng `alembic downgrade base` rồi `upgrade head`, nên cũng kiểm tra luôn chiều downgrade của migration.

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
