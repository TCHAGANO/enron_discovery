import django.db.models.deletion
from django.db import migrations, models


def fill_folder_path(apps, schema_editor):
    # Avant cette migration, un dossier etait identifie par son seul nom.
    Folder = apps.get_model("polls", "Folder")
    for folder in Folder.objects.all():
        folder.path = folder.name
        folder.save(update_fields=["path"])


def fill_normalized_subject(apps, schema_editor):
    # Calcule en SQL pour rester rapide sur ~500 000 lignes.
    schema_editor.execute(
        r"""
        UPDATE polls_email
        SET normalized_subject = LEFT(LOWER(TRIM(REGEXP_REPLACE(
            REGEXP_REPLACE(subject, '^\s*((re|fw|fwd|tr|aw|wg)\s*(\[\d+\])?\s*:\s*)+', '', 'i'),
            '\s+', ' ', 'g'))), 500)
        WHERE subject <> ''
        """
    )


class Migration(migrations.Migration):

    dependencies = [
        ("polls", "0004_alter_email_options_email_search_vector_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="Thread",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("normalized_subject", models.CharField(blank=True, max_length=500)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("last_activity", models.DateTimeField(blank=True, null=True)),
                ("email_count", models.PositiveIntegerField(default=0)),
            ],
            options={"ordering": ["-last_activity"]},
        ),
        migrations.AlterModelOptions(name="folder", options={"ordering": ["path"]}),
        migrations.AddField(
            model_name="folder",
            name="mailbox",
            field=models.CharField(blank=True, db_index=True, max_length=200),
        ),
        migrations.AddField(
            model_name="folder",
            name="path",
            field=models.CharField(default="", max_length=500),
            preserve_default=False,
        ),
        migrations.RunPython(fill_folder_path, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="folder",
            name="path",
            field=models.CharField(max_length=500, unique=True),
        ),
        migrations.AddField(
            model_name="email",
            name="normalized_subject",
            field=models.CharField(blank=True, db_index=True, max_length=500, default=""),
            preserve_default=False,
        ),
        migrations.RunPython(fill_normalized_subject, migrations.RunPython.noop),
        migrations.AddField(
            model_name="email",
            name="in_reply_to_header",
            field=models.CharField(blank=True, max_length=500, default=""),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="email",
            name="in_reply_to_inferred",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="email",
            name="thread",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="emails",
                to="polls.thread",
            ),
        ),
    ]
