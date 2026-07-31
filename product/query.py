"""Product query helpers."""

from product.models import Product


def active_products():
    return Product.objects.filter(is_active=True)
