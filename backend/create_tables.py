from app import app, db
from models import User
from sqlalchemy import inspect, text

with app.app_context():
    db.create_all()
    user_columns = {column['name'] for column in inspect(db.engine).get_columns('users')}
    if 'student_number' not in user_columns:
        db.session.execute(text('ALTER TABLE users ADD COLUMN student_number VARCHAR(50) NULL'))
        db.session.commit()
    user_columns = {column['name'] for column in inspect(db.engine).get_columns('users')}
    if 'employee_number' not in user_columns:
        db.session.execute(text('ALTER TABLE users ADD COLUMN employee_number VARCHAR(50) NULL'))
        db.session.commit()

    user_indexes = {index['name'] for index in inspect(db.engine).get_indexes('users')}
    if 'uq_users_student_number' not in user_indexes:
        db.session.execute(text('CREATE UNIQUE INDEX uq_users_student_number ON users (student_number)'))
        db.session.commit()
    user_indexes = {index['name'] for index in inspect(db.engine).get_indexes('users')}
    if 'uq_users_employee_number' not in user_indexes:
        db.session.execute(text('CREATE UNIQUE INDEX uq_users_employee_number ON users (employee_number)'))
        db.session.commit()

    print("数据库表创建成功！")
    
    if not User.query.filter_by(username='admin').first():
        admin = User(username='admin', employee_number='admin', email='admin@example.com', role='admin')
        admin.set_password('admin123')
        db.session.add(admin)
        db.session.commit()
        print("默认管理员账号创建成功：admin / admin123")
    else:
        admin = User.query.filter_by(username='admin').first()
        if not admin.employee_number:
            admin.employee_number = 'admin'
            db.session.commit()
            print("默认管理员教职工号已设置：admin")