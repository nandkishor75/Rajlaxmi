import frappe
from frappe import _
from frappe.utils import get_time
from frappe.utils import now_datetime
from frappe.utils import get_datetime
from frappe.utils import time_diff_in_hours, flt

def validate_checkin(doc, method):
    check_employee_checkin_time(doc, method)
    prevent_multiple_checkins_per_day(doc, method)
    handle_missing_checkout(doc,method)
    update_attendance_based_on_work_hours(doc, method)


def prevent_multiple_checkins_per_day(doc, method):
    if doc.log_type == "IN":
        existing_checkin = frappe.get_all(
            "Employee Checkin",
            filters={
                "employee": doc.employee,
                "time": ["between", [doc.time.date(), f"{doc.time.date()} 23:59:59"]],
                "log_type": "IN" 
            },
            fields=["name"]
        )
        
        if existing_checkin:
            frappe.throw(_("Employee has already checked in today. Multiple check-ins are not allowed."))


def update_attendance_based_on_work_hours(doc, method):
    start_of_day = get_datetime(f"{doc.time.date()} 09:00:00")
    end_of_day = get_datetime(f"{doc.time.date()} 18:59:59")

    checkin = frappe.get_value(
        "Employee Checkin", 
        {"employee": doc.employee, "log_type": "IN", "time": ["between", [start_of_day, end_of_day]]}, 
        "time"
    )
    
    checkout = frappe.get_value(
        "Employee Checkin", 
        {"employee": doc.employee, "log_type": "OUT", "time": ["between", [start_of_day, end_of_day]]}, 
        "time"
    )

    if checkin and checkout:
        hours_worked = time_diff_in_hours(checkout, checkin)
        status = "Absent"

        if hours_worked >= 9:
            status = "Present"
        elif hours_worked >= 4.5:
            status = "Half Day"

        attendance = frappe.get_doc({
            "doctype": "Attendance",
            "employee": doc.employee,
            "attendance_date": doc.time.date(),
            "status": status
        })
        attendance.insert(ignore_permissions=True)
        attendance.submit()


def check_employee_checkin_time(doc, method):
    if doc.log_type == "IN":
        checkin_time = get_time(doc.time)
        if checkin_time > get_time("15:30:00"):
            attendance = frappe.get_doc({
                "doctype": "Attendance",
                "employee": doc.employee,
                "attendance_date": doc.time.date(),
                "status": "Absent"
            })
            attendance.insert(ignore_permissions=True)
            attendance.submit()

            frappe.throw("Employee checked in after 3:30 PM. Marked as Absent.")


def handle_missing_checkout(doc, method):
    if frappe.flags.auto_checkout:
        return 

    frappe.flags.auto_checkout = True 

    employees = frappe.get_all(
        "Employee Checkin",
        filters={"log_type": "IN", "time": ["<", now_datetime()]},
        fields=["employee", "time"]
    )

    for emp in employees:
        checkin_date = emp["time"].date()
        start_of_day = get_datetime(f"{checkin_date} 09:00:00")
        end_of_day = get_datetime(f"{checkin_date} 18:00:00")

        checkout_exists = frappe.get_value(
            "Employee Checkin",
            {"employee": emp["employee"], "log_type": "OUT", "time": ["between", [start_of_day, end_of_day]]},
            "name"
        )

        if not checkout_exists:
            checkout_entry = frappe.get_doc({
                "doctype": "Employee Checkin",
                "employee": emp["employee"],
                "time": end_of_day,
                "log_type": "OUT"
            })
            checkout_entry.insert(ignore_permissions=True)
            checkout_entry.submit()

            attendance = frappe.get_doc({
                "doctype": "Attendance",
                "employee": emp["employee"],
                "attendance_date": checkin_date,
                "status": "Half Day"
            })
            attendance.insert(ignore_permissions=True)
            attendance.submit()

    frappe.flags.auto_checkout = False


def calculate_fractional_leave_deduction(employee, payroll_period):
    total_deduction = 0.0

    leave_applications = frappe.get_all("Leave Application",
        filters={
            "employee": employee,
            "status": "Approved",
            "from_date": [">=", payroll_period.start_date],
            "to_date": ["<=", payroll_period.end_date],
        },
        fields=["leave_type", "total_leave_days"]
    )

    for leave in leave_applications:
        leave_policy = frappe.get_value("Leave Type", leave.leave_type, "is_paid_leave")

        if not leave_policy:
            daily_wage = frappe.get_value("Employee", employee, "per_day_salary") or 0
            total_deduction += flt(leave.total_leave_days) * daily_wage

    return total_deduction

def update_salary_slip(doc, method):
    if doc.salary_slip_based_on_timesheet:
        return

    leave_deduction = calculate_fractional_leave_deduction(doc.employee, doc.payroll_period)
    
    if leave_deduction > 0:
        doc.total_deductions += leave_deduction
        doc.net_pay = max(0, doc.gross_pay - doc.total_deductions)
