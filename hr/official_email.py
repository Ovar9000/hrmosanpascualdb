"""The register of official gov.ph email accounts, and the requests for new ones (DECISIONS EM1-EM8).

The mailboxes are on the LGU's mail panel (`OFFICIAL_EMAIL_PANEL`: the GWHS cPanel, or DICT's GovMail), where
the Email Admin makes, resets and closes them. This module keeps the register and works out, from the HR records, which accounts
need a password reset or closing because their holder left: an office account the day its holder no
longer has an active appointment in that office, a personal account the day its owner has none anywhere
(EM2). Offices ask for accounts here; an Executive approves; the administrator's queue shows what to do in
the mail panel (EM6-EM8). Nothing here talks to the mail panel, stores a password or sends email.
"""

from django.conf import settings
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from .models import EmailAccount, EmailHandover, EmailRequest, Engagement, Office, Separation

Account = EmailAccount
RESET, CLOSE = "reset", "close"


class EmailRegisterError(Exception):
    """A register action that is not allowed; the message is shown to the user."""


def office_scoped(account):
    """An office account or an office's mailing list follows the holder's appointment in that office."""
    return account.kind != Account.Kind.PERSONAL and account.office_id is not None


def _due_filter():
    active = Engagement.objects.filter(person=OuterRef("holder"), status=Engagement.Status.ACTIVE)
    scoped = ~Q(kind=Account.Kind.PERSONAL) & Q(office__isnull=False)
    return (scoped & ~Exists(active.filter(office=OuterRef("office")))) | (~scoped & ~Exists(active))


def due():
    """Active accounts whose holder has left: reset the password (office account or list) or close it
    (personal account), each with the day the holder's last appointment there ended (EM2)."""
    rows = []
    qs = Account.objects.filter(status=Account.Status.ACTIVE, holder__isnull=False).filter(_due_filter())
    for account in qs.select_related("holder", "office").order_by("address"):
        engs = account.holder.engagements.exclude(status=Engagement.Status.ACTIVE)
        if office_scoped(account):
            engs = engs.filter(office=account.office_id)
        ends = [e.end_date for e in engs if e.end_date]
        rows.append({"account": account, "action": CLOSE if account.kind == Account.Kind.PERSONAL else RESET,
                     "left_on": max(ends) if ends else None})
    rows.sort(key=lambda r: (r["left_on"] is None, r["left_on"] or 0, r["account"].address))
    return rows


def attention_count():
    return Account.objects.filter(status=Account.Status.ACTIVE, holder__isnull=False).filter(_due_filter()).count()


def upcoming():
    """Accounts whose holder has a separation in progress: reset or close them on the last day (EM2)."""
    rows = {}
    open_seps = Separation.objects.filter(status=Separation.Status.CLEARING,
                                          person__email_accounts__status=Account.Status.ACTIVE)
    for sep in open_seps.distinct().select_related("person", "engagement").order_by("last_day"):
        for account in to_hand_over(sep):
            rows.setdefault(account.pk, {"account": account, "sep": sep,
                                         "action": CLOSE if account.kind == Account.Kind.PERSONAL else RESET})
    return sorted(rows.values(), key=lambda r: (r["sep"].last_day, r["account"].address))


def to_hand_over(sep):
    """Active accounts the separating person holds that this separation ends: their office accounts in
    the office they are leaving, and their personal accounts unless another appointment continues (EM4)."""
    accounts = list(sep.person.email_accounts.filter(status=Account.Status.ACTIVE).select_related("office"))
    leaving = [a for a in accounts if office_scoped(a) and a.office_id == sep.engagement.office_id]
    staying = sep.person.engagements.filter(status=Engagement.Status.ACTIVE).exclude(pk=sep.engagement_id).exists()
    return leaving + ([] if staying else [a for a in accounts if not office_scoped(a)])


def domain():
    return settings.OFFICIAL_EMAIL_DOMAIN.strip().lower().lstrip("@")


def panel():
    """Where the mailboxes are made, reset and closed, for the screens: the GWHS cPanel, or GovMail (EM1)."""
    return settings.OFFICIAL_EMAIL_PANEL


def suggested_address(office):
    """The usual office address: its acronym, or its first word, at the LGU's domain (EM5)."""
    local = (office.acronym or office.name.split()[0]).lower()
    local = "".join(c for c in local if c.isalnum() or c in ".-_")
    return f"{local}@{domain()}" if domain() and local else ""


def offices_without_account():
    """Offices with no active office account yet, with a suggested address, to start the register (EM5)."""
    covered = Account.objects.filter(kind=Account.Kind.OFFICE, status=Account.Status.ACTIVE).values("office")
    return [(o, suggested_address(o)) for o in Office.objects.exclude(pk__in=covered).order_by("name")]


def _check_date(account, done_on, today):
    if done_on is None:
        raise EmailRegisterError("Write the date it was done.")
    if done_on > today:
        raise EmailRegisterError("The date cannot be in the future.")
    if done_on < account.issued_on:
        raise EmailRegisterError("The date cannot be before the account was issued.")


def hand_over(account, user, to_holder, done_on, today, note=""):
    """Record a password reset done in the mail panel, with who holds the account now (EM3). Call inside a
    transaction with `account` locked; the caller bumps the version and saves it."""
    if not account.is_active:
        raise EmailRegisterError("This account is closed.")
    _check_date(account, done_on, today)
    if account.kind == Account.Kind.PERSONAL and to_holder != account.holder:
        raise EmailRegisterError("A personal account stays with its owner: reset it, or close it.")
    if to_holder is not None and office_scoped(account) and not to_holder.engagements.filter(
            office=account.office_id, status=Engagement.Status.ACTIVE).exists():
        raise EmailRegisterError(f"{to_holder.full_name} has no active appointment in {account.office.name}.")
    same = to_holder == account.holder
    EmailHandover.objects.create(
        account=account, action=EmailHandover.Action.RESET if same else EmailHandover.Action.HANDOVER,
        from_holder=account.holder, to_holder=to_holder, done_on=done_on, note=note[:255], recorded_by=user,
    )
    account.holder, account.password_reset_on = to_holder, done_on


def close(account, user, done_on, today, note=""):
    """Record that the account was closed in the mail panel (EM3)."""
    if not account.is_active:
        raise EmailRegisterError("This account is already closed.")
    _check_date(account, done_on, today)
    EmailHandover.objects.create(account=account, action=EmailHandover.Action.CLOSED, from_holder=account.holder,
                                 to_holder=None, done_on=done_on, note=note[:255], recorded_by=user)
    account.status, account.closed_on = Account.Status.CLOSED, done_on


# --- Requests (EM6-EM8) ---------------------------------------------------------------------------


def approver_name(user):
    return (user.get_full_name() or user.get_username()) if user else ""


def file_request(req, user):
    """Save a new request: a new account waits for an Executive's approval; a closing goes straight to the
    Email Admin (EM6)."""
    if req.type == EmailRequest.Type.CLOSE:
        if req.account is None or not req.account.is_active:
            raise EmailRegisterError("Choose an active account to close.")
        if EmailRequest.objects.filter(account=req.account, type=EmailRequest.Type.CLOSE,
                                       status__in=EmailRequest.OPEN).exists():
            raise EmailRegisterError(f"A request to close {req.account.address} is already open.")
        req.kind, req.office, req.person = req.account.kind, req.account.office, req.account.holder
        req.status = EmailRequest.Status.APPROVED
    else:
        req.account = None
        req.status = EmailRequest.Status.PENDING
    req.requested_by = user
    req.save()
    return req


def decide(req, user, approve, note=""):
    """An Executive approves or declines a new account (EM7). Never their own request."""
    if req.status != EmailRequest.Status.PENDING:
        raise EmailRegisterError("This request is not waiting for approval.")
    if req.requested_by_id == user.pk:
        raise EmailRegisterError("Another approver decides on a request you filed.")
    if not approve and not note:
        raise EmailRegisterError("Write why it is declined.")
    req.status = EmailRequest.Status.APPROVED if approve else EmailRequest.Status.DECLINED
    req.decided_by, req.decided_at, req.decision_note = user, timezone.now(), note[:255]


def not_done(req, user, note):
    """The Email Admin cannot do an approved request (EM8), e.g. the address is taken."""
    if req.status != EmailRequest.Status.APPROVED:
        raise EmailRegisterError("Only an approved request can be turned down here.")
    if not note:
        raise EmailRegisterError("Write why it cannot be done.")
    req.status, req.done_by, req.done_at = EmailRequest.Status.DECLINED, user, timezone.now()
    req.decision_note = note[:255]


def withdraw(req):
    if not req.is_open:
        raise EmailRegisterError("This request is already closed.")
    req.status = EmailRequest.Status.WITHDRAWN


def complete(req, user, account):
    """Mark a request done once the account is made or closed in the mail panel and on the register (EM8)."""
    if req.status != EmailRequest.Status.APPROVED:
        raise EmailRegisterError("Only an approved request can be done.")
    req.account, req.status, req.done_by, req.done_at = account, EmailRequest.Status.DONE, user, timezone.now()


def close_for_request(req, user, done_on, today, note=""):
    """Close the account in the register for an approved closing request (EM8). Call inside a transaction with
    `req` locked; the caller bumps the request's version and saves it."""
    account = Account.objects.select_for_update().get(pk=req.account_id)
    close(account, user, done_on, today, note or f"Request {req.number}")
    account.version += 1
    account.save()
    complete(req, user, account)


def waiting_for(user, can_approve, can_do):
    """Requests waiting on this user: new accounts to approve (not their own), approved ones to do (EM8)."""
    count = 0
    if can_approve:
        count += EmailRequest.objects.filter(status=EmailRequest.Status.PENDING).exclude(requested_by=user).count()
    if can_do:
        count += EmailRequest.objects.filter(status=EmailRequest.Status.APPROVED).count()
    return count
