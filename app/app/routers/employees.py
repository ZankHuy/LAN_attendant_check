from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db
from app import crud
from app.schemas import (
    EmployeeCreate,
    EmployeeUpdate,
    EmployeeResponse,
    DepartmentCreate,
    DepartmentResponse,
)

router = APIRouter(prefix="/api/employees", tags=["employees"])


def _to_emp_response(emp) -> EmployeeResponse:
    return EmployeeResponse(
        id=emp.id,
        code=emp.code,
        name=emp.name,
        department_id=emp.department_id,
        department_name=emp.department.name if emp.department else None,
        start_date=emp.start_date,
        status_label=emp.status_label,
    )


@router.get("", response_model=list[EmployeeResponse])
def list_employees(db: Session = Depends(get_db)):
    """Lấy danh sách tất cả nhân viên"""
    return [_to_emp_response(e) for e in crud.get_employees(db)]


@router.post("", response_model=EmployeeResponse)
def create_employee(data: EmployeeCreate, db: Session = Depends(get_db)):
    """Thêm nhân viên mới"""
    for e in crud.get_employees(db):
        if e.code == data.code:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Mã nhân viên {data.code} đã tồn tại",
            )
    emp = crud.create_employee(
        db,
        code=data.code,
        name=data.name,
        department_id=data.department_id,
        start_date=data.start_date,
        status_label=data.status_label,
    )
    db.refresh(emp)
    return _to_emp_response(emp)


@router.put("/{employee_id}", response_model=EmployeeResponse)
def update_employee(
    employee_id: int,
    data: EmployeeUpdate,
    db: Session = Depends(get_db),
):
    """Cập nhật nhân viên"""
    # Distinguish "field not in payload" vs "field set to null" so we can
    # properly clear nullable columns (e.g. unassign department).
    sent = data.get_set_fields()
    emp = crud.update_employee(
        db,
        employee_id=employee_id,
        code=data.code if 'code' in sent else None,
        name=data.name if 'name' in sent else None,
        department_id=data.department_id if ('department_id' in sent and data.department_id is not None) else None,
        start_date=data.start_date if ('start_date' in sent and data.start_date is not None) else None,
        status_label=data.status_label if 'status_label' in sent else None,
        clear_department=('department_id' in sent and data.department_id is None),
        clear_start_date=('start_date' in sent and data.start_date is None),
    )
    if not emp:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy nhân viên",
        )
    db.refresh(emp)
    return _to_emp_response(emp)


@router.delete("/{employee_id}")
def delete_employee(employee_id: int, db: Session = Depends(get_db)):
    """Xóa nhân viên"""
    if not crud.delete_employee(db, employee_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy nhân viên",
        )
    return {"message": "Đã xóa nhân viên"}


# ----- Department endpoints (mounted under /api/employees/departments) -----
@router.get("/departments/all", response_model=list[DepartmentResponse])
def list_departments(db: Session = Depends(get_db)):
    return crud.get_departments(db)


@router.post("/departments", response_model=DepartmentResponse)
def create_department(data: DepartmentCreate, db: Session = Depends(get_db)):
    for d in crud.get_departments(db):
        if d.name == data.name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Phòng ban '{data.name}' đã tồn tại",
            )
    return crud.create_department(db, data.name, data.sort_order)


@router.delete("/departments/{department_id}")
def delete_department(department_id: int, db: Session = Depends(get_db)):
    if not crud.delete_department(db, department_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy phòng ban",
        )
    return {"message": "Đã xóa phòng ban"}