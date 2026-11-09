"""Purchase-order PDF for an approved request (reportlab). The file is written to the shared PO
directory and served to authorized users by the gateway (`GET /v1/files/po/{pr_id}`)."""

from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from common.security import sha256_hex
from common.settings import get_settings


def render_po(pr: dict[str, Any]) -> tuple[str, str]:
    """Write the PDF; returns (path, sha256)."""
    out_dir = get_settings().po_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{pr['id']}.pdf"
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(
        str(path), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, title=f"PO {pr['id']}"
    )
    rows = [["#", "SKU", "Description", "Qty", "Unit price", "Amount"]]
    for it in pr["line_items"]:
        rows.append(
            [
                str(it["line_no"]),
                it["sku"],
                Paragraph(it["description"], styles["BodyText"]),
                str(it["qty"]),
                f"{it['unit_price']:,.2f}",
                f"{it['qty'] * it['unit_price']:,.2f}",
            ]
        )
    rows.append(["", "", "", "", "Total (USD)", f"{pr['total']:,.2f}"])
    table = Table(rows, colWidths=[8 * mm, 28 * mm, 70 * mm, 14 * mm, 24 * mm, 26 * mm], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -2), 0.25, colors.HexColor("#d1d5db")),
                ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    approvals = (
        "; ".join(f"{a['level']}: {a['approver']} ({a['decision']})" for a in pr["approvals"])
        or "auto-approved"
    )
    story = [
        Paragraph("Acme Corp — Purchase Order", styles["Title"]),
        Paragraph(
            f"<b>PO number:</b> PO-{pr['id'].removeprefix('PR-')} &nbsp;&nbsp; <b>Request:</b> {pr['id']}",
            styles["Normal"],
        ),
        Paragraph(
            f"<b>Vendor:</b> {pr['vendor_name']} &nbsp;&nbsp; <b>Department:</b> {pr['department_id']}",
            styles["Normal"],
        ),
        Paragraph(
            f"<b>Requested by:</b> {pr['requester_name']} &nbsp;&nbsp; <b>Status:</b> {pr['status']}",
            styles["Normal"],
        ),
        Spacer(1, 6 * mm),
        table,
        Spacer(1, 6 * mm),
        Paragraph(f"<b>Approvals:</b> {approvals}", styles["Normal"]),
        Paragraph(f"<b>Justification:</b> {pr['justification']}", styles["Normal"]),
    ]
    doc.build(story)
    return str(path), sha256_hex(path.read_bytes())
