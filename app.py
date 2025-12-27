import os
from flask import Flask, render_template, request, redirect, session, jsonify
from pymongo import MongoClient
from bson import ObjectId
from werkzeug.security import generate_password_hash, check_password_hash

from dotenv import load_dotenv

load_dotenv()  # loads variables from .env into environment

# ---------------- FLASK APP SETUP ----------------
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY")  # use secret from .env

MONGO_URI = os.environ.get("MONGO_URI_ATLAS")
client = MongoClient(MONGO_URI)
db = client.get_database()  # will now pick 'college_portal' from URI

# TEST CONNECTION
try:
    client.admin.command("ping")
    print("MongoDB Atlas connected ✅")
except Exception as e:
    print("MongoDB connection failed ❌", e)

students = db["students"]
results = db["results"]
queries = db["queries"]
attendance = db["attendance"]
admins = db["admins"]

# ---------------- SAFE ADMIN SETUP ----------------
if not admins.find_one({"email": "admin@gmail.com"}):
    admins.insert_one({
        "email": "admin@gmail.com",
        "password": generate_password_hash("admin@1234"),
        "role": "admin"
    })

# ---------------- CONSTANTS ----------------
GRADE_POINTS = {
    "O": 10, "A+": 9, "A": 8, "B+": 7,
    "B": 6, "C": 5, "U": 0, "W": 0
}

def attendance_grade(p):
    p = float(p)
    if p >= 95:
        return "O"
    elif p >= 85:
        return "M"
    elif p >= 75:
        return "S"
    else:
        return "U"

# ---------------- ADMIN ROUTES ----------------
@app.route("/admin", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]
        admin = admins.find_one({"email": email})
        if admin and check_password_hash(admin["password"], password):
            session["admin"] = admin["email"]
            return redirect("/admin/dashboard")
        return "Invalid Admin Login"
    return render_template("admin_login.html")

@app.route("/admin/dashboard")
def admin_dashboard():
    if "admin" not in session:
        return redirect("/admin")

    all_students = list(students.find())

    # SAME departments logic as admin_reports
    departments = {}
    for s in all_students:
        dept = s.get("department", "N/A")
        departments[dept] = departments.get(dept, 0) + 1

    department_students = [
        {"_id": k, "student_count": v}
        for k, v in departments.items()
    ]

    total_students = len(all_students)
    total_queries = queries.count_documents({})
    total_departments = len(department_students)
    

    return render_template(
        "admin_dashboard.html",
        total_students=total_students,
        total_departments=total_departments,
        total_queries=total_queries,
        department_students=department_students
    )


@app.route("/admin/add-student", methods=["GET","POST"])
def admin_add_student():
    if "admin" not in session:
        return redirect("/admin")
    if request.method == "POST":
        register_no = request.form["register_no"]
        email = request.form["email"]
        if students.find_one({"$or":[{"register_no": register_no}, {"email": email}]}):
            return "Student already exists"
        students.insert_one({
            "register_no": register_no,
            "name": request.form["name"],
            "email": email,
            "password": generate_password_hash(request.form["password"]),
            "department": request.form["department"],
            "batch": request.form["batch"],
            "dob": request.form["dob"],
            "gender": request.form["gender"]
        })
        return redirect("/admin/add-student")
    all_students = list(students.find())
    for s in all_students:
        s["_id"] = str(s["_id"])
    return render_template("admin_add_student.html", students=all_students)

@app.route("/admin/attendance", methods=["GET","POST"])
def admin_attendance():
    if "admin" not in session:
        return redirect("/admin")
    if request.method == "POST":
        reg = request.form["register_no"]
        dept = request.form["department"]
        semester = request.form["semester"]
        subject = request.form["subject"]
        total_classes = int(request.form["total"])
        attended_classes = int(request.form["attended"])
        attendance_percentage = round((attended_classes / total_classes) * 100, 2) if total_classes > 0 else 0
        attendance.update_one(
            {"register_no": reg, "semester": semester, "subject": subject},
            {"$set": {
                "department": dept,
                "total_classes": total_classes,
                "attended_classes": attended_classes,
                "attendance_percentage": attendance_percentage
            }},
            upsert=True
        )
        return "Attendance uploaded successfully"
    return render_template("admin_attendance.html")

@app.route("/get-student-department/<register_no>")
def get_student_department(register_no):
    student = students.find_one({"register_no": register_no}, {"_id": 0, "department": 1})
    return jsonify({"department": student.get("department") if student else ""})

@app.route("/get-student-attendance/<register_no>/<semester>/<subject>")
def get_student_attendance(register_no, semester, subject):
    record = attendance.find_one({
        "register_no": register_no,
        "semester": semester,
        "subject": subject
    }, {"_id": 0, "attendance_percentage": 1})
    return jsonify({"percentage": record.get("attendance_percentage", 0) if record else 0})

@app.route("/admin/delete-student/<register_no>")
def admin_delete_student(register_no):
    if "admin" not in session:
        return redirect("/admin")
    students.delete_one({"register_no": register_no})
    results.delete_many({"register_no": register_no})
    attendance.delete_many({"register_no": register_no})
    return redirect("/admin/add-student")

@app.route("/admin/edit-profile/<register_no>", methods=["GET","POST"])
def admin_edit_profile(register_no):
    if "admin" not in session:
        return redirect("/admin")
    student = students.find_one({"register_no": register_no})
    if request.method == "POST":
        students.update_one({"register_no": register_no}, {"$set": {
            "name": request.form["name"],
            "email": request.form["email"],
            "department": request.form["department"],
            "batch": request.form.get("batch", student.get("batch")),
            "dob": request.form.get("dob", student.get("dob")),
            "gender": request.form.get("gender", student.get("gender"))
        }})
        return redirect("/admin/add-student")
    return render_template("admin_edit_profile.html", student=student)

@app.route("/admin/upload", methods=["GET","POST"])
def upload_result():
    if "admin" not in session:
        return redirect("/admin")
    if request.method == "POST":
        reg = request.form["register_no"]
        sem = int(request.form["semester"])
        subjects = []
        total_points = 0
        total_credits = 0
        overall_pass = True
        for i in range(1, 6):
            code = request.form.get(f"code{i}")
            if code:
                name = request.form[f"name{i}"]
                grade = request.form[f"grade{i}"].strip().upper()
                credit = int(request.form[f"credit{i}"])
                att = float(request.form[f"att{i}"])
                grade_point = GRADE_POINTS.get(grade, 0)
                att_grade = attendance_grade(att)
                result_status = "FAIL" if grade in ["U","W"] else "PASS"
                if result_status == "FAIL": overall_pass = False
                subjects.append({
                    "code": code,
                    "name": name,
                    "credit": credit,
                    "grade": grade,
                    "grade_point": grade_point,
                    "attendance_percentage": att,
                    "attendance_grade": att_grade,
                    "result": result_status
                })
                total_points += grade_point * credit
                total_credits += credit
        gpa = round(total_points / total_credits, 2) if total_credits>0 else 0
        final_result = "PASS" if overall_pass else "FAIL"
        results.insert_one({
            "register_no": reg,
            "semester": sem,
            "subjects": subjects,
            "total_credits": total_credits,
            "gpa": gpa,
            "final_result": final_result
        })
        return "Result Uploaded Successfully"
    return render_template("admin_upload_result.html")

@app.route("/admin/update-student/<register_no>", methods=["POST"])
def admin_update_student(register_no):
    if "admin" not in session:
        return redirect("/admin")
    students.update_one(
        {"register_no": register_no},
        {"$set": {
            "name": request.form["name"],
            "email": request.form["email"],
            "department": request.form["department"],
            "batch": request.form["batch"],
            "dob": request.form["dob"],
            "gender": request.form["gender"]
        }}
    )
    return redirect("/admin/add-student")

@app.route("/admin/reports")
def admin_report():
    if "admin" not in session:
        return redirect("/admin")
    all_students = list(students.find())
    report = []
    departments = {}
    for s in all_students:
        reg = s["register_no"]
        name = s["name"]
        dept = s.get("department", "N/A")
        student_attendance = list(attendance.find({"register_no": reg}))
        attendance_posted = len(student_attendance) > 0
        student_results = list(results.find({"register_no": reg}))
        result_summary = []
        for r in student_results:
            for sub in r.get("subjects", []):
                result_summary.append({
                    "semester": r.get("semester"),
                    "subject": sub.get("name"),
                    "grade": sub.get("grade"),
                    "attendance_grade": sub.get("attendance_grade"),
                    "result": sub.get("result")
                })
        departments[dept] = departments.get(dept, 0) + 1
        student_query = queries.find_one({"register_no": reg})
        query_status = student_query.get("status", "Pending") if student_query else None
        report.append({
            "register_no": reg,
            "name": name,
            "department": dept,
            "attendance_posted": attendance_posted,
            "result_summary": result_summary,
            "query_status": query_status
        })
    department_students = [{"_id": k, "student_count": v} for k, v in departments.items()]
    total_students = len(all_students)
    total_queries = queries.count_documents({})
    return render_template(
        "admin_reports.html",
        report=report,
        total_students=total_students,
        total_queries=total_queries,
        department_students=department_students
    )

@app.route("/admin/queries")
def admin_queries():
    if "admin" not in session:
        return redirect("/admin")
    all_queries = list(queries.find().sort("created_at", -1))
    for q in all_queries:
        student = students.find_one({"register_no": q["register_no"]}, {"_id":0, "department":1, "batch":1})
        q["department"] = student.get("department","N/A") if student else "N/A"
        q["batch"] = student.get("batch","N/A") if student else "N/A"
    return render_template("admin_queries.html", queries=all_queries)

@app.route("/admin/reply/<id>", methods=["POST"])
def admin_reply(id):
    if "admin" not in session:
        return redirect("/admin")
    reply_text = request.form.get("reply")
    if not reply_text:
        return "Reply cannot be empty"
    queries.update_one({"_id": ObjectId(id)}, {"$set":{"reply": reply_text, "status": "Replied"}})
    return redirect("/admin/queries")

# ---------------- STUDENT ROUTES ----------------
@app.route("/student/register", methods=["GET","POST"])
def student_register():
    if request.method == "POST":
        students.insert_one({
            "register_no": request.form["register_no"],
            "name": request.form["name"],
            "email": request.form["email"],
            "password": generate_password_hash(request.form["password"])
        })
        return redirect("/student/login")
    return render_template("student_register.html")

@app.route("/student/login", methods=["GET","POST"])
def student_login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]
        s = students.find_one({"email": email})
        if s and check_password_hash(s["password"], password):
            session["reg"] = s["register_no"]
            return redirect("/student/dashboard")
        return "Invalid Student Login"
    return render_template("student_login.html")

@app.route("/student/dashboard")
def student_dashboard():
    if "reg" not in session:
        return redirect("/student/login")
    reg = session["reg"]
    student = students.find_one({"register_no": reg})
    all_results = list(results.find({"register_no": reg}))
    student_attendance = list(attendance.find({"register_no": reg}))
    student_queries = list(queries.find({"register_no": reg}))
    pending_queries = queries.count_documents({"register_no": reg, "status":"Pending"})
    avg_gpa = round(sum(r["gpa"] for r in all_results)/len(all_results),2) if all_results else 0
    subjects = [sub["name"] for r in all_results for sub in r.get("subjects", [])] if all_results else []
    return render_template("student_dashboard.html",
                           student=student,
                           all_results=all_results,
                           attendance=student_attendance,
                           queries=student_queries,
                           pending_queries=pending_queries,
                           avg_gpa=avg_gpa,
                           attendance_count=len(student_attendance),
                           subjects=subjects)

@app.route("/student/summary")
def student_summary():
    if "reg" not in session:
        return redirect("/student/login")
    reg = session["reg"]
    student = students.find_one({"register_no": reg})
    all_results = list(results.find({"register_no": reg}))
    student_attendance = list(attendance.find({"register_no": reg}))
    student_queries = list(queries.find({"register_no": reg}))
    pending_queries = queries.count_documents({"register_no": reg, "status": "Pending"})
    avg_gpa = round(sum(r["gpa"] for r in all_results)/len(all_results), 2) if all_results else 0
    cgpa = avg_gpa
    subjects = [sub["name"] for r in all_results for sub in r.get("subjects", [])] if all_results else []
    return render_template(
        "student_summary.html",
        student=student,
        all_results=all_results,
        attendance=student_attendance,
        queries=student_queries,
        pending_queries=pending_queries,
        avg_gpa=avg_gpa,
        cgpa=cgpa,
        attendance_count=len(student_attendance),
        subjects=subjects
    )

@app.route("/student/profile")
def student_profile():
    if "reg" not in session:
        return redirect("/student/login")
    student = students.find_one({"register_no": session["reg"]})
    return render_template("student_profile.html", student=student)

@app.route("/student/result")
def student_result():
    if "reg" not in session:
        return redirect("/student/login")
    reg = session["reg"]
    student = students.find_one({"register_no": reg})
    all_results = list(results.find({"register_no": reg}).sort("semester",1))
    cgpa = round(sum(r["gpa"] for r in all_results)/len(all_results),2) if all_results else 0
    return render_template("student_result.html", student=student, all_results=all_results, cgpa=cgpa)

@app.route("/student/attendance")
def student_attendance():
    if "reg" not in session:
        return redirect("/student/login")
    data = list(attendance.find({"register_no": session["reg"]}))
    return render_template("student_attendance.html", data=data)

@app.route("/student/queries")
def student_queries():
    if "reg" not in session:
        return redirect("/student/login")
    data = list(queries.find({"register_no": session["reg"]}))
    return render_template("student_queries.html", queries=data)

@app.route("/student/query", methods=["GET","POST"])
def student_query():
    if "reg" not in session:
        return redirect("/student/login")
    student = students.find_one({"register_no": session["reg"]})
    if request.method == "POST":
        queries.insert_one({
            "register_no": student["register_no"],
            "name": student["name"],
            "query_type": request.form["query_type"],
            "message": request.form["message"],
            "reply": "",
            "status": "Pending"
        })
        return "Query sent successfully"
    return render_template("student_query.html")

# ---------------- OTHER ROUTES ----------------
@app.route("/")
def index():
    total_students = students.count_documents({})
    pass_count = results.count_documents({"final_result":"PASS"})
    fail_count = results.count_documents({"final_result":"FAIL"})
    return render_template("index.html",
                           total_students=total_students,
                           pass_count=pass_count,
                           fail_count=fail_count)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


@app.route("/test-insert")
def test_insert():
    students.insert_one({"register_no": "999", "name": "Test", "email": "test@test.com"})
    return "Inserted Test Student ✅"


# ---------------- RUN APP ----------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)


