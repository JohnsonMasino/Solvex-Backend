from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("wallet", "0001_initial"),
    ]

    operations = [
        # ---- Deposit: usdt_amount -> amount (data is kept) ----
        migrations.RenameField(
            model_name="deposit",
            old_name="usdt_amount",
            new_name="amount",
        ),
        migrations.AlterField(
            model_name="deposit",
            name="amount",
            field=models.DecimalField(
                decimal_places=8, max_digits=24,
                help_text="Amount in the asset's own units (USDT / BTC / ETH).",
            ),
        ),
        migrations.AlterField(
            model_name="deposit",
            name="rate",
            field=models.DecimalField(
                decimal_places=6, max_digits=18,
                help_text="USD per 1 unit of the asset, frozen at creation.",
            ),
        ),
        migrations.AlterField(
            model_name="deposit",
            name="usd_amount",
            field=models.DecimalField(
                decimal_places=2, max_digits=18,
                help_text="Frozen: amount x rate.",
            ),
        ),
        migrations.AddField(
            model_name="deposit",
            name="asset",
            field=models.CharField(
                choices=[("usdt", "USDT"), ("btc", "BTC"), ("eth", "ETH")],
                default="usdt", max_length=10,
            ),
        ),
        migrations.AlterField(
            model_name="deposit",
            name="network",
            field=models.CharField(
                choices=[
                    ("bep20", "USDT (BEP20 – BNB Smart Chain)"),
                    ("erc20", "USDT (ERC20 – Ethereum)"),
                    ("trc20", "USDT (TRC20 – TRON)"),
                    ("btc", "BTC (Bitcoin)"),
                    ("eth", "ETH (Ethereum)"),
                ],
                max_length=10,
            ),
        ),

        # ---- Withdrawal: fee columns ----
        migrations.AddField(
            model_name="withdrawal",
            name="fee_percent",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=5),
        ),
        migrations.AddField(
            model_name="withdrawal",
            name="fee_usd",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=18),
        ),
        migrations.AddField(
            model_name="withdrawal",
            name="total_usd",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=18),
        ),
    ]