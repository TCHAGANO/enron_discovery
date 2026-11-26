from django.contrib import admin

from .models import Attachment, Email, Employee, Folder, Thread


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ("name", "email")
    search_fields = ("name", "email")


@admin.register(Folder)
class FolderAdmin(admin.ModelAdmin):
    list_display = ("path", "mailbox", "name")
    list_filter = ("name",)
    search_fields = ("path",)


@admin.register(Thread)
class ThreadAdmin(admin.ModelAdmin):
    list_display = ("normalized_subject", "email_count", "started_at", "last_activity")
    search_fields = ("normalized_subject",)


class AttachmentInline(admin.TabularInline):
    model = Attachment
    extra = 0


@admin.register(Email)
class EmailAdmin(admin.ModelAdmin):
    list_display = ("subject", "from_employee", "date", "folder")
    list_filter = ("date",)
    search_fields = ("subject", "message_id")
    raw_id_fields = ("from_employee", "to_employees", "cc_employees", "bcc_employees", "in_reply_to", "folder", "thread")
    exclude = ("search_vector",)
    inlines = [AttachmentInline]
