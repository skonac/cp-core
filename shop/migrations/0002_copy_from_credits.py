# Products and purchases move from the credits app (CreditPackage, Purchase) to shop (Product,
# Purchase), with the same ids. credits.0004 then drops the old tables.

from django.db import migrations

COPY = """
INSERT INTO shop_product (id, name, title, description, credits, price, currency, is_active, is_featured,
                          sort_order, created_at)
SELECT id, name, title, description, credits, price, currency, is_active, is_featured, sort_order, created_at
FROM credits_creditpackage;

INSERT INTO shop_purchase (id, user_id, product_id, product_name, credits, list_price, amount, currency, status,
                           provider, conversation_id, provider_token, provider_payment_id, provider_response,
                           error_code, error_message, ip, created_at, paid_at)
SELECT id, user_id, package_id, package_name, credits, list_price, amount, currency, status,
       provider, conversation_id, provider_token, provider_payment_id, provider_response,
       error_code, error_message, ip, created_at, paid_at
FROM credits_purchase;

SELECT setval(pg_get_serial_sequence('shop_product', 'id'), COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM shop_product;
SELECT setval(pg_get_serial_sequence('shop_purchase', 'id'), COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM shop_purchase;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("shop", "0001_initial"),
        ("credits", "0003_package_texts_purchase"),
    ]

    operations = [migrations.RunSQL(COPY, migrations.RunSQL.noop)]
