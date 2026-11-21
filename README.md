# VieWallet

Ví điện tử fintech **mô phỏng**, xây dựng như một sản phẩm thật: ledger kế toán kép, state machine giao dịch, idempotency, kiểm soát rủi ro, hệ thống vận hành và trợ lý tài chính AI chạy bằng model miễn phí.

> Không có tiền thật, ngân hàng thật, API thanh toán thật hay dữ liệu cá nhân thật. Mọi luồng tiền đi qua ledger nội bộ và Payment Provider Simulator.

## Trạng thái

| Phase | Nội dung | Trạng thái |
|---|---|---|
| 0 | Product discovery & kiến trúc | Đã duyệt |
| 1 | Project foundation (backend core, frontend shell, Docker, CI) | Hoàn tất, chờ nghiệm thu |
| 2 → 13 | Theo [roadmap](docs/roadmap.md) | Chưa bắt đầu |

## Công nghệ

- **Backend**: Python 3.14, FastAPI, SQLAlchemy 2 (async, psycopg 3), Alembic, PostgreSQL 18, Redis, structlog. Modular monolith gồm hai process: `api` và `worker`.
- **Frontend**: pnpm monorepo gồm app khách hàng (`apps/web`) và app quản trị (`apps/admin`), dùng chung design system (`packages/ui`). Stack: React 19, TypeScript, Vite, Tailwind CSS 4, TanStack Query, React Router, i18next (vi/en).
- **Hạ tầng**: Docker Compose, nginx, GitHub Actions, Playwright.

## Bắt đầu nhanh

```bash
cp .env.example .env
docker compose up -d --build
```

Sau đó mở:

- App khách hàng: <http://127.0.0.1:8080>
- App admin: <http://127.0.0.1:8081>
- Tài liệu API: <http://127.0.0.1:8000/api/docs>

Phát triển, chạy test, CI: [docs/development/setup.md](docs/development/setup.md).

## Tài liệu

Bắt đầu từ [docs/README.md](docs/README.md): PRD, kiến trúc, thiết kế xử lý tài chính, bảo mật, AI, design system, đặc tả API, chiến lược kiểm thử, roadmap và ADR. Tài liệu viết bằng tiếng Việt.
