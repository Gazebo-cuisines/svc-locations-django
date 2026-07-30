# Product API Release Checklist

## 1) Migrations
- Run `python manage.py migrate`
- Confirm `product.0003` and `product.0004` are applied

## 2) Smoke API checks
- `GET /product/class/`, `/category/`, `/range/`, `/unit/` -> 200
- `GET /product/:id/` -> 200 for known product
- Optional satellites (`technical`, `costing`, `shelf-life`, `stock-policy`, `packaging`, `production`, `nutrition`, `ingredient-label`, `acceptance`) return:
  - 200 with row data when set
  - 200 with `data: null` and `...not set yet` message when unset

## 3) Timeline checks
- Create product -> event logged
- Update product/satellite -> event logged
- Delete/deactivate action -> event logged
- `GET /product/:id/timeline/` returns history entries

## 4) Frontend contract
- No separate audit write API call needed
- FE only performs normal create/update/delete calls
- FE should treat optional satellite GET empty state as valid (`data: null`)
- FE should render timeline from `GET /product/:id/timeline/`

## 5) Postman
- Use `postman/Gazebo-Locations-Products.postman_collection.json`
- Verify requests in:
  - Product Lookups
  - Products
  - Product Satellites
