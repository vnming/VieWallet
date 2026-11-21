# ADR-0010: Python 3.14, PostgreSQL 18, Node 24, Docker Desktop

- Trạng thái: Accepted (2026-11-21)
- Ngày: 2026-11-21

## Quyết định

- Python 3.14 (đã cài trên máy; có sẵn `uuid.uuid7()`), quản lý bằng uv với lockfile. Chuyển về 3.13 nếu dependency bắt buộc nào chưa có wheel cho 3.14 (kiểm tra ở Phase 1).
- PostgreSQL 18 (container).
- Node 24, pnpm 9+ (đã cài trên máy).
- Redis 8 (hoặc Valkey) container.
- Docker Desktop (backend WSL 2) chạy PostgreSQL, Redis, Mailpit và test. Đã cài: client/server 29.9.3, Compose v5.7.0 (kiểm tra 2026-11-21).

## Kết quả kiểm tra ở Phase 1

- Python 3.14: mọi dependency có wheel (FastAPI 0.165, SQLAlchemy 2.1, psycopg 3.3, Alembic 1.20, pydantic 2.14). Giữ 3.14, không cần về 3.13.
- Frontend: Node 24, pnpm 9.15.4, Vite 8, React 19, React Router 8. TypeScript ghim ở 6.0 vì typescript-eslint chỉ hỗ trợ `<6.1.0` (TypeScript 7 đã phát hành).
- psycopg chế độ async không chạy được trên `ProactorEventLoop` mặc định của Windows: worker, seed và pytest dùng `SelectorEventLoop` khi chạy trên Windows. Trong container (Linux) không bị ảnh hưởng.

## Hệ quả

- Repo nằm trong thư mục đồng bộ cloud nên hardlink lỗi: uv cấu hình `link-mode = "copy"`.
- Máy dev có hai PostgreSQL cài trực tiếp chiếm cổng 5432 và 5433: container Postgres publish ra `127.0.0.1:55432`.
- Trên Windows, `localhost` được phân giải sang `::1` trước, trong khi Docker chỉ publish cổng ở `127.0.0.1`, khiến kết nối bị treo: mọi URL dev dùng `127.0.0.1`.
- Docker CLI nằm ở `C:\Users\MINH\AppData\Local\Programs\DockerDesktop\resources\bin` (cài theo user). Terminal mở trước khi cài cần khởi động lại để nhận PATH.
- RAM 12 GB là giới hạn khi chạy cùng lúc Docker stack và Ollama; cấu hình bộ nhớ WSL 2 và dùng compose profile để bật/tắt service.
