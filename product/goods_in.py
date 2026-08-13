from product.models import Category, Product, ProductGoodsInType

_ROOT_GOODS_IN_TYPE = {
    'raw materials': ProductGoodsInType.RAW_MATERIAL,
    'packaging materials': ProductGoodsInType.PACKAGING,
}


def category_root(category: Category) -> Category:
    current = category
    seen = {current.id}
    while current.parent_id is not None and current.parent_id not in seen:
        parent = Category.objects.filter(pk=current.parent_id).first()
        if parent is None:
            break
        seen.add(parent.id)
        current = parent
    return current


def goods_in_type_from_category(category: Category) -> str:
    root_name = (category_root(category).name or '').strip().lower()
    return _ROOT_GOODS_IN_TYPE.get(root_name, ProductGoodsInType.OTHER)


def effective_goods_in_type(product: Product) -> str:
    if product.goods_in_type:
        return product.goods_in_type
    if product.category_id:
        return goods_in_type_from_category(product.category)
    return ProductGoodsInType.OTHER
