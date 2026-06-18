from django.db import migrations


class Migration(migrations.Migration):
    # No-op: the Secret model (created by 0102_add_secret_storage) was removed when
    # the JTL KEK encryption infrastructure was stripped from this fork (commit
    # bb08e2a) — but 0102 was deleted while this AlterField was left behind, leaving a
    # dangling reference to a non-existent model that broke `migrate` on any fresh DB
    # (KeyError: ('providers', 'secret')). The operation is dropped (the table no
    # longer exists); the migration node is kept to preserve the graph + dependents
    # (0110_merge_0108_branches).
    dependencies = [("providers", "0107_update_system_connection_fk")]

    operations = []
