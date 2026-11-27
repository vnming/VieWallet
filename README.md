# VieWallet

Ví điện tử fintech **mô phỏng**, xây dựng như một sản phẩm thật: ledger kế toán kép, state machine giao dịch, idempotency, kiểm soát rủi ro, hệ thống vận hành và trợ lý tài chính AI chạy bằng model miễn phí.

> Không có tiền thật, ngân hàng thật, API thanh toán thật hay dữ liệu cá nhân thật. Mọi luồng tiền đi qua ledger nội bộ và Payment Provider Simulator.

## Trạng thái

| Phase | Nội dung | Trạng thái |
|---|---|---|
| 0 | Product discovery & kiến trúc | Hoàn tất |
| 1 | Project foundation (backend core, frontend shell, Docker, CI) | Hoàn tất |
| 2 | Định danh & bảo mật (đăng nhập, MFA nhân viên, PIN) | Hoàn tất |
| 3 | Ví & lõi tài chính (ledger kế toán kép) | Hoàn tất |
| 4 | Provider Simulator, nạp và rút tiền | Hoàn tất |
| 5 | Chuyển tiền, thanh toán QR, hoàn tiền, đảo giao dịch | Hoàn tất |
| 6 | KYC mô phỏng, hạn mức, rủi ro | Hoàn tất |
| 7 | Hoàn thiện app khách hàng (hồ sơ, xuất CSV, trợ giúp, hỗ trợ) | Hoàn tất |
| 8 | Admin & Operations | Hoàn tất |
| 9 → 13 | Theo [roadmap](docs/roadmap.md) | Chưa bắt đầu |

## Công nghệ

- **Backend**: Python 3.14, FastAPI, SQLAlchemy 2 (async, psycopg 3), Alembic, PostgreSQL 18, Redis, structlog. Modular monolith gồm hai process: `api` và `worker`. Payment Provider Simulator là service riêng (`backend/sim`).
- **Frontend**: pnpm monorepo gồm app khách hàng (`apps/web`) và app quản trị (`apps/admin`), dùng chung design system (`packages/ui`). Stack: React 19, TypeScript, Vite, Tailwind CSS 4, TanStack Query, React Router, i18next (vi/en).
- **Hạ tầng**: Docker Compose, nginx, GitHub Actions, Playwright.

## Cách chạy nhanh nhất: toàn bộ stack bằng Docker

Cách này chỉ cần Docker, không cần cài Python hay Node.

### 1. Cài công cụ

| Công cụ | Ghi chú |
|---|---|
| Git | Để clone repo |
| Docker Desktop (hoặc Docker Engine + Compose v2) | Đã kiểm tra với Docker 29, Compose v5. Trên Windows bật WSL 2 |
| RAM trống | Khoảng 4 GB cho cả stack; build image nên chạy khi máy không mở quá nhiều ứng dụng nặng |

Cổng cần trống trên máy: `8080` (app khách hàng), `8081` (app admin), `8000` (API), `8090` (simulator), `8025` (Mailpit), `55432` (Postgres), `6379` (Redis). Nếu trùng, đổi trong file `.env` ở bước 3.

### 2. Clone repo

```bash
git clone https://github.com/vnming/VieWallet.git
cd VieWallet
```

### 3. Tạo file `.env`

```bash
cp .env.example .env
```

Trên PowerShell: `Copy-Item .env.example .env`.

File `.env.example` đã có sẵn giá trị dev nên chạy được ngay. Các giá trị này **chỉ dành cho máy local**: không dùng lại ở nơi khác và không commit file `.env`. Muốn tự sinh khóa mã hóa mới:

```bash
python -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

rồi dán vào `VW_DATA_ENCRYPTION_KEY` và `VW_PIN_PEPPER`. Lưu ý: đổi hai khóa này sau khi đã có dữ liệu sẽ làm dữ liệu cũ (PIN, giấy tờ KYC) không đọc được.

### 4. Build và khởi động

```bash
docker compose up -d --build
```

Lần đầu mất vài phút (build image backend, frontend). Compose khởi động theo thứ tự: Postgres, Redis, Mailpit, simulator, rồi service `migrate` (chạy migration và seed dữ liệu tham chiếu, sau đó tự thoát với mã 0), rồi `api`, `worker`, `web`, `admin`.

Kiểm tra mọi thứ đã sẵn sàng:

```bash
docker compose ps          # api, simulator, postgres, redis ở trạng thái healthy; migrate đã exited (0)
bash e2e/wait-for-stack.sh # chờ đến khi API và hai app trả lời (cần bash và curl)
```

Nếu `migrate` báo lỗi, xem log bằng `docker compose logs migrate`.

### 5. Mở ứng dụng

| Dịch vụ | URL |
|---|---|
| App khách hàng | <http://127.0.0.1:8080> |
| App admin (nhân viên) | <http://127.0.0.1:8081> |
| Tài liệu API (OpenAPI) | <http://127.0.0.1:8000/api/docs> |
| Mailpit (hộp thư email dev) | <http://127.0.0.1:8025> |
| SimBank, ngân hàng mô phỏng | <http://127.0.0.1:8090/health> |

> **Windows:** luôn dùng `127.0.0.1`, đừng dùng `localhost` (IPv6 có thể làm kết nối tới Docker bị treo).

## Dùng thử lần đầu

Mọi thao tác dưới đây dùng dữ liệu hư cấu. Dùng email dạng `ten@example.com` (tên miền dành riêng như `.local` bị từ chối).

### A. Tạo khách hàng và nạp tiền

1. Mở <http://127.0.0.1:8080> → **Đăng ký**, nhập email, họ tên và mật khẩu (nên dùng cụm từ dài, ví dụ `may-trang-bay-2026`).
2. Mở Mailpit <http://127.0.0.1:8025>, mở email xác minh và bấm liên kết hoặc nhập mã 6 số.
3. Đăng nhập, đặt **PIN giao dịch** 6 số khi được yêu cầu.
4. **Nạp tiền** → nhập số tiền (ví dụ `500000`) → chuyển sang trang SimBank → **Duyệt thanh toán** → quay lại ví, số dư đã cập nhật.

Mẹo kịch bản thử lỗi của simulator: số tiền quyết định kết quả. Ví dụ `111000` thất bại, `222000` hoàn tất sau một lúc, `777000` ngân hàng không khả dụng. Bảng đầy đủ ở [docs/architecture/payment-provider-simulator.md](docs/architecture/payment-provider-simulator.md).

### B. Tạo tài khoản nhân viên (app admin)

Không có đăng ký công khai cho nhân viên; tạo bằng CLI trong container `api`. Mật khẩu đọc từ stdin để không nằm trong lịch sử shell.

Bash / Git Bash:

```bash
echo "may-trang-bay-2026" | docker compose exec -T api python -m app.scripts.create_staff \
    --email admin@example.com --name "Admin Demo" --role SUPER_ADMIN
```

PowerShell:

```powershell
"may-trang-bay-2026" | docker compose exec -T api python -m app.scripts.create_staff `
    --email admin@example.com --name "Admin Demo" --role SUPER_ADMIN
```

Role hợp lệ: `SUPPORT`, `OPERATIONS`, `FINANCE`, `RISK_ANALYST`, `KYC_REVIEWER`, `ADMIN`, `SUPER_ADMIN` (tham số `--role` dùng được nhiều lần).

Sau đó mở <http://127.0.0.1:8081>, đăng nhập. Lần đầu bắt buộc bật **xác thực hai lớp (TOTP)**: quét mã bằng Google Authenticator, Aegis hoặc 1Password, nhập mã 6 số, rồi lưu các mã khôi phục.

Các thao tác cần hai người (điều chỉnh số dư, duyệt hoàn tiền, đổi phí, gán role) đòi hỏi **hai nhân viên khác nhau**: một người đề xuất, người kia duyệt. Hãy tạo thêm một nhân viên với email khác.

### C. Các lệnh tạo dữ liệu demo khác

```bash
# Khách hàng đã xác minh email, không cần đăng ký
echo "may-trang-bay-2026" | docker compose exec -T api python -m app.scripts.create_customer \
    --email an@example.com --name "Nguyễn Văn An"

# Cửa hàng mô phỏng (nhận thanh toán QR, hoàn tiền)
echo "may-trang-bay-2026" | docker compose exec -T api python -m app.scripts.create_merchant \
    --create-user --email cafe@example.com --owner "Lê Thu Mây" --name "Cà phê Mây (mô phỏng)"

# Nâng KYC tier 1 (cần để rút tiền); chỉ dùng được ở development/simulation
docker compose exec -T api python -m app.scripts.set_kyc_tier --email an@example.com --tier 1
```

Các tính năng khác (chuyển tiền, QR, KYC, rủi ro, hỗ trợ, đối soát, bảng điều khiển simulator) được mô tả từng bước trong [docs/development/setup.md](docs/development/setup.md).

## Dừng, chạy lại, xóa dữ liệu

```bash
docker compose stop            # dừng, giữ dữ liệu
docker compose up -d           # chạy lại
docker compose down            # xóa container, giữ dữ liệu (volume)
docker compose down -v         # XÓA CẢ DỮ LIỆU local (database, tệp KYC)
```

Sau khi `git pull` code mới: `docker compose up -d --build`. Nếu chỉ build lại riêng service `api`, chạy thêm `docker compose restart web admin worker` để nginx nhận địa chỉ mới của `api`.

## Chạy khi phát triển (app chạy trên máy, hạ tầng trong Docker)

Cần thêm: [uv](https://docs.astral.sh/uv/) (tự cài Python 3.14), Node.js 24 và pnpm 9.15.4.

```bash
cp .env.example .env
cp backend/.env.example backend/.env       # cùng mật khẩu với .env ở gốc
cd backend && uv sync && cd ..
cd frontend && pnpm install && cd ..

docker compose up -d postgres redis mailpit

# Terminal 1: API
cd backend
uv run alembic upgrade head
uv run python -m app.scripts.seed
uv run uvicorn --factory app.main:app_factory --reload --port 8000

# Terminal 2: worker (job nền: outbox, hết hạn giao dịch, đối soát...)
cd backend && uv run python -m app.worker

# Terminal 3: Payment Provider Simulator
cd backend
uv run alembic -c sim_alembic.ini upgrade head
uv run uvicorn --factory sim.main:app_factory --port 8090

# Terminal 4 và 5: giao diện
cd frontend && pnpm dev:web        # http://127.0.0.1:5173
cd frontend && pnpm dev:admin      # http://127.0.0.1:5174
```

Khi chạy kiểu này, CLI không đi qua Docker mà chạy trực tiếp, ví dụ `cd backend && uv run python -m app.scripts.create_staff ...` (mật khẩu vẫn đọc từ stdin).

## Kiểm tra chất lượng

```bash
# Backend (cần Postgres và Redis đang chạy)
cd backend && uv run ruff check . && uv run mypy app && uv run lint-imports && uv run pytest

# Frontend
cd frontend && pnpm format:check && pnpm lint && pnpm typecheck && pnpm test && pnpm build

# E2E (Playwright): dựng stack với thời gian chờ rút ngắn rồi chạy
VW_NEW_DESTINATION_COOLDOWN_MINUTES=0 SIM_PENDING_DELAY_SECONDS=5 SIM_SLOW_RESPONSE_SECONDS=15 \
    docker compose up -d --build
cd e2e && pnpm install && pnpm exec playwright install chromium && pnpm test
```

Chi tiết từng lệnh, biến môi trường và CI: [docs/development/setup.md](docs/development/setup.md). Máy ít RAM nên chạy test với `--workers=2` hoặc `--workers=1` và build từng app frontend một.

## Xử lý sự cố thường gặp

| Triệu chứng | Cách xử lý |
|---|---|
| `docker` không nhận lệnh (Windows) | Mở lại terminal hoặc thêm `C:\Users\<user>\AppData\Local\Programs\DockerDesktop\resources\bin` vào PATH |
| Trình duyệt treo khi mở `localhost` | Dùng `127.0.0.1` |
| Cổng bị chiếm (`port is already allocated`) | Đổi cổng `VW_PORT_*` trong `.env`, rồi `docker compose up -d` lại. Postgres mặc định dùng `55432` để tránh trùng PostgreSQL cài sẵn ở `5432` |
| `api` không khởi động, `migrate` lỗi | `docker compose logs migrate`; thường do `.env` thiếu biến hoặc volume Postgres cũ tạo từ phiên bản trước. Chỉ khi chấp nhận mất dữ liệu local: `docker compose down -v` rồi `up -d --build` |
| Trang web/admin trả 502 sau khi build lại `api` | `docker compose restart web admin worker` |
| Không thấy email xác minh | Mở Mailpit <http://127.0.0.1:8025> (email dev không gửi ra ngoài) |
| Build frontend báo hết bộ nhớ | Đóng bớt ứng dụng, build từng app: `cd frontend/apps/admin && pnpm build` |
| Nạp tiền ở SimBank chờ rất lâu | Mặc định simulator chờ 120 giây cho kịch bản trễ; đặt `SIM_PENDING_DELAY_SECONDS=5` khi `docker compose up` để rút ngắn |

## Tài liệu

Bắt đầu từ [docs/README.md](docs/README.md): PRD, kiến trúc, thiết kế xử lý tài chính, bảo mật, AI, design system, đặc tả API, chiến lược kiểm thử, roadmap và ADR. Tài liệu viết bằng tiếng Việt. Quy tắc làm việc của dự án nằm ở [CLAUDE.md](CLAUDE.md).
