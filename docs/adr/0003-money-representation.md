# ADR-0003: Tiền là số nguyên đơn vị nhỏ nhất; chuỗi trong JSON

- Trạng thái: Accepted (2026-11-21)
- Ngày: 2026-11-21

## Quyết định

- Lưu trữ: `BIGINT` đơn vị nhỏ nhất + `CHAR(3)` tiền tệ. `currencies.exponent` xác định đơn vị nhỏ nhất (VND = 0).
- Python: value object `Money(amount_minor: int, currency)`. Không có `float` trên đường đi của tiền. `Decimal` chỉ được dùng bên trong phép tính phí nếu rule tương lai cần, và được chuyển về số nguyên với quy tắc làm tròn tường minh.
- Phí phần trăm tính theo basis point, làm tròn half-up bằng số học nguyên.
- API: số tiền là chuỗi thập phân của đơn vị nhỏ nhất (`"1250000"`) kèm tiền tệ. Frontend định dạng bằng `Intl.NumberFormat` và helper an toàn với BigInt.

## Hệ quả

- Không có sai số dấu phẩy động; bất biến viết bằng SQL đơn giản.
- Sẵn sàng đa tiền tệ (exponent theo tiền tệ); tính toán khác tiền tệ là lỗi kiểu.
- Client không được coi số tiền là number để tính logic (đã ghi tài liệu; type sinh tự động dùng `string`).
