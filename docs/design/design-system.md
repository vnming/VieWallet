# Đặc tả UI/UX và Design System

| Mục | Giá trị |
|---|---|
| Trạng thái | Bản nháp chờ duyệt (Phase 0). Khám phá hình ảnh và tinh chỉnh token diễn ra ở Phase 1 với component thật. |
| Cập nhật | 2026-11-21 |
| Liên quan | [Luồng người dùng](../product/user-flows.md), [Thông báo & PWA](../architecture/notifications-pwa.md) |

## 1. Định hướng thiết kế: "Jade Ledger"

VieWallet cần cảm giác điềm tĩnh, chính xác và đáng tin — gần với một công cụ ngân hàng được làm kỹ hơn là một siêu ứng dụng đầy khuyến mãi. Nhận diện độc lập với các ví Việt Nam hiện có (không dùng hồng, không dùng màn hình splash "xanh thương hiệu" rực, không có linh vật).

- **Cảm xúc**: nền mực đậm, điểm nhấn màu ngọc bích (jade), sắc giấy ấm trung tính, nhiều khoảng trắng, con số là nhân vật chính.
- **Dấu ấn**: (1) số dư lớn dùng chữ số đều cột (tabular) với hiệu ứng đếm nhẹ khi thay đổi; (2) mô-típ "đường sổ cái" — đường phân cách mảnh và vạch dọc màu jade đánh dấu mục đang chọn; (3) trạng thái giao dịch hiển thị dạng timeline nhỏ, không chỉ là badge.
- **Giọng văn (vi)**: ngắn, rõ, lịch sự ("Chuyển tiền thành công", không lạm dụng dấu chấm than). Không dark pattern, không tạo cảm giác gấp gáp giả.

## 2. Nguyên tắc UX

1. **Tiền phải rõ trước tiên**: số tiền, chiều (+/−), trạng thái và đối tác đọc được trong một cái nhìn; chữ số tabular; ký hiệu tiền sau số (`1.250.000 ₫`, định dạng vi-VN).
2. **Xác nhận điều quan trọng**: mọi thao tác trừ tiền có màn hình xem lại (người nhận, số tiền, phí, tổng, nguồn tiền) trước khi nhập PIN.
3. **Giải thích trạng thái**: mọi trạng thái chưa kết thúc hoặc thất bại có lời giải thích đơn giản và bước tiếp theo.
4. **Bảo mật hiện diện nhưng bình tĩnh**: sự kiện bảo mật hiển thị trong timeline riêng; cảnh báo dùng mẫu mức độ nhất quán.
5. **Mobile-first, thuận ngón cái**: thao tác chính ở vùng dưới; vùng chạm tối thiểu 44×44 px.
6. **Tiếp cận mặc định**: tương phản WCAG 2.2 AA, focus hiển thị rõ, có nhãn, không bao giờ chỉ dùng màu để thể hiện trạng thái, tôn trọng giảm chuyển động.
7. **Cảm giác nhanh**: skeleton cho nội dung; optimistic UI chỉ cho thao tác không liên quan tài chính (đánh dấu đã đọc, đổi tên), không bao giờ cho số dư.

## 3. Token

Ba lớp: **primitive → semantic → component**. Cài đặt bằng CSS custom property, dùng qua `@theme` của Tailwind v4. Màu dùng OKLCH; giá trị cuối được kiểm tra tương phản ở Phase 1.

### 3.1 Màu primitive (tham khảo)

| Thang | 50 | 100 | 300 | 500 | 600 | 700 | 900 | 950 |
|---|---|---|---|---|---|---|---|---|
| `jade` (thương hiệu) | oklch(97% .02 170) | oklch(94% .04 170) | oklch(82% .09 170) | oklch(63% .12 172) | oklch(55% .11 174) | oklch(47% .09 176) | oklch(30% .05 180) | oklch(22% .04 182) |
| `ink` (trung tính lạnh) | oklch(98% .003 250) | oklch(95% .005 250) | oklch(85% .01 250) | oklch(60% .015 250) | oklch(48% .018 252) | oklch(38% .02 254) | oklch(21% .02 258) | oklch(15% .02 260) |
| `paper` (trung tính ấm) | oklch(99% .005 85) | oklch(97% .01 85) | — | — | — | — | — | — |
| `saffron` (nhấn, điểm sáng) | oklch(97% .03 85) | | | oklch(78% .15 75) | oklch(70% .15 65) | | | |
| `success` | | | | oklch(62% .15 145) | | | | |
| `warning` | | | | oklch(75% .15 70) | | | | |
| `danger` | | | | oklch(58% .2 25) | | | | |
| `info` | | | | oklch(60% .13 240) | | | | |

> Token `--color-saffron-700` = oklch(51% .12 65): chữ trên nền `saffron-100` đạt ≥ 4,5:1 (Lighthouse, Phase 7).

Màu success lệch về xanh-vàng (hue 145) để khác biệt với jade (hue ~172), và trạng thái luôn đi kèm icon và nhãn.

### 3.2 Token semantic

`--color-bg`, `--color-bg-subtle`, `--color-surface`, `--color-surface-raised`, `--color-border`, `--color-border-strong`, `--color-text`, `--color-text-muted`, `--color-text-inverse`, `--color-primary`, `--color-primary-hover`, `--color-primary-fg`, `--color-accent`, `--color-focus-ring`, `--color-amount-in` (success-700), `--color-amount-out` (text), `--color-status-{pending|processing|completed|failed|cancelled|expired|reversed|refunded}-{bg|fg|border}`.

Theme: sáng (nền paper, chữ ink) và tối (nền ink-950, primary jade-300). Mặc định theo hệ thống, user đổi được trong cài đặt.

### 3.3 Typography

- **Be Vietnam Pro** (giấy phép OFL, thiết kế cho dấu tiếng Việt) cho UI và con số, tự host qua `@fontsource` (không dùng CDN font bên ngoài). `font-variant-numeric: tabular-nums` cho mọi số tiền và bảng.
- **JetBrains Mono** cho mã tham chiếu, số ví, mã code.
- Thang chữ (rem, tỷ lệ 1.25, mobile/desktop): `display` 2.5/3.25 (số dư), `h1` 1.75/2, `h2` 1.375/1.5, `h3` 1.125/1.25, `body` 1, `body-sm` .875, `caption` .75. Line-height 1.5 cho body, 1.2 cho heading. Độ đậm 400/500/600/700.

### 3.4 Khoảng cách, bo góc, độ nổi, bố cục

- Khoảng cách: đơn vị 4 px (`0.5, 1, 1.5, 2, 3, 4, 5, 6, 8, 10, 12, 16` × 4 px).
- Bo góc: `sm 6`, `md 10`, `lg 14`, `xl 20` (card), `full` (chip/avatar).
- Bóng: `sm` đường mảnh (0 1 2 / 6 %), `md` card (0 4 12 / 8 %), `lg` lớp phủ (0 12 32 / 14 %); theme tối dùng viền + độ sáng bề mặt thay cho bóng đậm.
- Breakpoint: `sm 640`, `md 768`, `lg 1024`, `xl 1280`, `2xl 1536`.
- App khách hàng: một cột khi ≤ 640 với thanh tab dưới (Trang chủ, Lịch sử, Quét, Báo cáo, Tài khoản); ≥ 1024 dùng điều hướng bên + lưới 12 cột, nội dung rộng tối đa 1120.
- App admin: sidebar trái (thu gọn được), thanh trên có tìm kiếm toàn cục và badge môi trường (DEV/SIM màu hổ phách), nội dung tối đa 1440, chế độ bảng dày đặc.

### 3.5 Chuyển động

| Token | Giá trị | Dùng cho |
|---|---|---|
| `duration-fast` | 120 ms | hover, nhấn |
| `duration-base` | 200 ms | dropdown, tab, toast |
| `duration-slow` | 320 ms | drawer, trang, modal |
| `ease-standard` | cubic-bezier(.2,0,0,1) | phần lớn |
| `ease-emphasized` | cubic-bezier(.3,0,0,1.1) | phản hồi thành công |

Animation có ý nghĩa: chuyển trang (mờ dần + trượt 8 px), drawer/sheet trượt, modal phóng 0.98→1, số dư đếm lên (≤ 600 ms, chỉ khi giá trị từ server thay đổi), dấu tích vẽ ra khi giao dịch thành công, skeleton shimmer, biểu đồ vẽ vào (một lần), badge thông báo nháy một lần. Khi `prefers-reduced-motion: reduce`, chuyển cảnh thành fade tức thì và tắt đếm số. Thư viện: Motion (`motion/react`), CSS khi đủ.

### 3.6 Icon và minh họa

lucide-react (nét 1.75 nhất quán), bộ icon riêng cho loại giao dịch (nạp, rút, chuyển vào/ra, thanh toán, hoàn tiền, đảo, điều chỉnh). Minh họa trạng thái trống: nét đơn giản màu jade/ink, không dùng ảnh stock.

## 4. Component (packages/ui)

Xây trên Radix primitive (qua mã nguồn component shadcn/ui, đổi style theo token của dự án). Mỗi component được ghi lại các trạng thái (mặc định, hover, focus, active, disabled, loading, error) và lưu ý tiếp cận. Storybook là tùy chọn (quyết định ở Phase 1); nếu không dùng thì route `/design` trong dev đóng vai style guide sống.

| Nhóm | Component |
|---|---|
| Hành động | Button (primary, secondary, ghost, danger, link; size sm/md/lg; loading), IconButton, ActionTile (thao tác nhanh ở trang chủ) |
| Nhập liệu | TextField, PasswordField (hiện/ẩn), **AmountInput** (nhóm số vi-VN, chip nhanh 100k/500k/1M, tối đa, gợi ý theo API hạn mức), PinPad (6 ô, bàn phím số, rung phản hồi khi hỗ trợ), ô nhập OTP, Select, Combobox (tìm người nhận), DatePicker/DateRange, Switch, Checkbox, Radio, Textarea, FileDrop (KYC) |
| Hiển thị | Card, BalanceCard (nút riêng tư), StatTile, Badge, **StatusBadge** (theo trạng thái giao dịch), **StatusTimeline**, **TransactionRow**, **AmountText** (dấu, màu, tabular), Avatar, WalletNumber (sao chép), QRCodeView, KeyValueList, Tag |
| Phản hồi | Toast, InlineAlert (info/success/warning/danger), Banner (offline, môi trường), ProgressSteps, bộ Skeleton, Spinner |
| Lớp phủ | Dialog, ConfirmDialog (thao tác phá hủy), Drawer/BottomSheet, Popover, Tooltip, DropdownMenu |
| Điều hướng | BottomTabBar, SideNav, TopBar, Tabs, Breadcrumbs, Pagination (admin), Stepper |
| Dữ liệu | DataTable (TanStack Table: sắp xếp, lọc, ẩn/hiện cột, độ dày, thao tác theo dòng, CSV qua backend), FilterBar, EmptyState, ErrorState (có nút thử lại + correlation id), wrapper biểu đồ (Line, Bar, Donut, Sparkline) kèm bảng thay thế cho trình đọc màn hình |
| Mẫu | ReviewAndConfirm (tóm tắt trước khi trừ tiền), ResultScreen (thành công/đang xử lý/thất bại kèm bước tiếp theo), SecurityEventItem, NotificationItem, ChatMessage + DataCard (AI), ApprovalPanel (xem khác biệt payload cho maker-checker) |

### 4.1 Component trạng thái giao dịch

| Trạng thái | Icon | Màu | Nhãn (vi) |
|---|---|---|---|
| CREATED | circle-dashed | ink | Chờ xác nhận |
| PENDING | clock | warning | Đang chờ xử lý |
| PROCESSING | loader | info | Đang xử lý |
| COMPLETED | check-circle | success | Thành công |
| FAILED | x-circle | danger | Thất bại |
| CANCELLED | ban | ink-muted | Đã hủy |
| EXPIRED | timer-off | ink-muted | Hết hạn |
| PARTIALLY_REFUNDED | rotate-ccw | info | Hoàn một phần |
| REFUNDED | rotate-ccw | info | Đã hoàn tiền |
| REVERSED | undo-2 | warning | Đã đảo giao dịch |

## 5. Trạng thái mọi màn hình phải có

Loading (skeleton giống bố cục), Empty (minh họa + một hành động), Error (thông báo + thử lại + correlation id để báo support), Success, Offline (với thao tác cần mạng), Không có quyền (admin), Dữ liệu một phần (một widget lỗi không được làm hỏng cả trang).

## 6. Danh sách trang

### 6.1 App khách hàng

| Route | Mục đích | Component chính |
|---|---|---|
| `/login`, `/register`, `/verify-email`, `/forgot-password`, `/reset-password` | Xác thực | TextField, PasswordField, InlineAlert |
| `/onboarding/pin` | Đặt PIN | PinPad |
| `/` Trang chủ | Số dư, thao tác nhanh (Nạp, Chuyển, Quét, Rút), giao dịch gần đây, gợi ý insight, banner KYC | BalanceCard, ActionTile, TransactionRow |
| `/wallet` | Chi tiết ví, số ví, QR nhận tiền, hạn mức, tiền đang giữ, khóa ví | WalletNumber, QRCodeView |
| `/deposit` → `/deposit/:ref` | Số tiền + phương thức → chuyển sang SimBank → kết quả | AmountInput, ResultScreen |
| `/withdraw` | Tài khoản nhận, số tiền, phí, xem lại, PIN | ReviewAndConfirm, PinPad |
| `/transfer` | Người nhận (tìm/QR/đã lưu), số tiền, ghi chú, xem lại, PIN, kết quả | Combobox, AmountInput, ReviewAndConfirm |
| `/scan` | Quét QR bằng camera (QR cá nhân hoặc thanh toán merchant) | QR scanner |
| `/pay/:code` | Xem lại payment request + PIN | ReviewAndConfirm |
| `/request` | Tạo yêu cầu xin tiền / hiện QR của tôi | QRCodeView |
| `/history`, `/history/:ref` | Danh sách có bộ lọc; chi tiết có timeline, nút hỗ trợ | FilterBar, StatusTimeline |
| `/reports` | Tóm tắt tháng, donut danh mục, cột theo ngày, ngân sách, mục tiêu, xuất file | Biểu đồ, StatTile |
| `/assistant` | Chat AI có data card, danh sách hội thoại | ChatMessage, DataCard |
| `/notifications` | Hộp thư, bộ lọc, đánh dấu đã đọc | NotificationItem |
| `/account`, `/account/profile`, `/account/security`, `/account/sessions`, `/account/kyc`, `/account/destinations`, `/account/recipients`, `/settings` | Hồ sơ, bảo mật (mật khẩu, PIN, phiên, lịch sử đăng nhập), KYC, cài đặt (ngôn ngữ, theme, thông báo, chế độ riêng tư, cài đặt app) | KeyValueList, SecurityEventItem |
| `/help`, `/help/:slug`, `/support/tickets` | Help center, ticket | — |
| `/merchant/*` (tài khoản merchant) | Payment request, thanh toán đã nhận, hoàn tiền | DataTable |

### 6.2 App admin

`/login` (+ MFA), `/` dashboard, `/users`, `/users/:id` (tab: tổng quan, ví, giao dịch, KYC, bảo mật, rủi ro, ticket, audit), `/wallets`, `/transactions`, `/transactions/:ref` (timeline, journal, provider operation, callback, rủi ro, liên quan, audit), `/operations` (hàng đợi), `/approvals`, `/refunds`, `/reversals`, `/adjustments`, `/kyc` hàng đợi + `/kyc/:id` duyệt, `/risk/alerts`, `/risk/rules`, `/reconciliation`, `/reconciliation/:run`, `/tickets`, `/reports`, `/notifications` (template, broadcast), `/ai` (model, prompt, mức sử dụng), `/simulator`, `/config` (phí, hạn mức, cài đặt), `/roles`, `/audit`, `/admin-activity`, `/system` (sức khỏe).

## 7. Checklist tiếp cận (cho mỗi component và trang)

HTML ngữ nghĩa, input có nhãn, thông báo lỗi liên kết qua `aria-describedby`, giữ focus trong dialog, `aria-live` cho toast và kết quả giao dịch, hỗ trợ bàn phím cho bảng và menu, tương phản ≥ 4.5:1 cho chữ / 3:1 cho UI, số tiền được đọc kèm đơn vị ("một triệu hai trăm năm mươi nghìn đồng" qua `Intl` + văn bản cho trình đọc màn hình), biểu đồ có bảng dữ liệu thay thế. Kiểm tra tự động: `axe` trong component test và Playwright.

## 9. Bảy trạng thái của một trang khách hàng (Phase 7)

Mọi trang của app khách hàng phải xử lý đủ 7 trạng thái; tiêu đề trang (h1) luôn hiện, kể cả khi đang tải hoặc lỗi.

| # | Trạng thái | Cách làm | Kiểm chứng |
|---|---|---|---|
| 1 | Đang tải | Skeleton đúng hình khối nội dung, giữ tiêu đề trang | Test component |
| 2 | Trống | `EmptyState` có lời giải thích và bước tiếp theo (liên kết hành động) | Test component |
| 3 | Lỗi | `ErrorState` có nút thử lại; lỗi API hiện tiêu đề đã dịch và correlation id | `a11y.test.tsx` quét 21 trang khi mọi API lỗi |
| 4 | Có dữ liệu / thành công | Nội dung, toast cho thao tác thành công | Test component, E2E |
| 5 | Ngoại tuyến | `OfflineBanner` toàn cục (`navigator.onLine`), dữ liệu cũ vẫn xem được | Test component |
| 6 | Phiên hết hạn / chưa đăng nhập | `RequireAuth` chuyển về `/login`; trợ giúp xem được khi chưa đăng nhập | Test router (Phase 2), E2E |
| 7 | Không tìm thấy / không có quyền | 404 cho tài nguyên không thuộc mình (không lộ sự tồn tại), trang 404 | Test API và component |

Tiếp cận: axe-core không có vi phạm serious/critical trên 21 trang ở trạng thái có dữ liệu và trạng thái lỗi (jsdom, `apps/web/src/routes/a11y.test.tsx`), và trên trình duyệt thật kèm kiểm tra tương phản màu (`e2e/tests/web.account.spec.ts`, `@axe-core/playwright`).
