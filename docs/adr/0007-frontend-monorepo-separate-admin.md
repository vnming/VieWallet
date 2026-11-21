# ADR-0007: Monorepo pnpm, app khách hàng và app admin tách riêng

- Trạng thái: Accepted (2026-11-21)
- Ngày: 2026-11-21

## Quyết định

`frontend/` là pnpm workspace: `apps/web` (PWA khách hàng), `apps/admin` (app nhân viên, không phải PWA), `packages/ui` (design system), `packages/api-client` (type sinh từ OpenAPI + client), `packages/shared`.

## Lý do

- Code và route admin không bao giờ được gửi tới khách hàng. Origin riêng cho phép CSP và cookie chặt hơn, và giới hạn IP cho admin.
- Design system và type API dùng chung mà không phải sao chép.

## Hệ quả

- Cài đặt nhiều hơn một chút (workspace, hai cấu hình Vite).
- CI phải chạy bước sinh OpenAPI → TypeScript để type luôn đồng bộ.
