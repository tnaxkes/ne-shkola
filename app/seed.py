from sqlalchemy.orm import Session

from app.models import Master, MasterService, Service


MASTERS_DATA = [
    {
        "name": "Алексей Громов",
        "is_active": True,
    },
    {
        "name": "Мария Темникова",
        "is_active": True,
    },
    {
        "name": "Дмитрий Кравцов",
        "is_active": True,
    },
]

SERVICES_DATA = [
    # Индивидуальные занятия
    {
        "category": "Индивидуальные занятия",
        "name": "Пробное занятие",
        "price": 1500,
        "duration": 60,
        "description": "Первое знакомство с инструментом или оценка текущего уровня. Без опыта — идеально, с опытом — разберём что доработать.",
        "is_active": True,
    },
    {
        "category": "Индивидуальные занятия",
        "name": "Занятие для начинающих",
        "price": 2500,
        "duration": 60,
        "description": "Постановка рук, освоение базовых ритмов, координация. Учимся с нуля без лишней теории.",
        "is_active": True,
    },
    {
        "category": "Индивидуальные занятия",
        "name": "Занятие среднего уровня",
        "price": 2500,
        "duration": 60,
        "description": "Полиритмия, сложные грувы, работа со свингом. Для тех, кто уже уверенно держит ритм.",
        "is_active": True,
    },
    {
        "category": "Индивидуальные занятия",
        "name": "Занятие продвинутого уровня",
        "price": 3000,
        "duration": 60,
        "description": "Индивидуальная программа: скорость, импровизация, разбор сложного материала.",
        "is_active": True,
    },
    {
        "category": "Индивидуальные занятия",
        "name": "Двойная сессия",
        "price": 4500,
        "duration": 120,
        "description": "Углублённая работа над конкретной техникой или подготовка к выступлению.",
        "is_active": True,
    },
    # Групповые занятия
    {
        "category": "Групповые занятия",
        "name": "Группа для новичков",
        "price": 1200,
        "duration": 60,
        "description": "До 4 человек. Базовые ритмы, простые рисунки, много практики в живой группе.",
        "is_active": True,
    },
    {
        "category": "Групповые занятия",
        "name": "Джазовый ансамбль",
        "price": 1500,
        "duration": 90,
        "description": "Играем в ансамбле: барабаны + клавиши + бас. Импровизация и взаимодействие с музыкантами.",
        "is_active": True,
    },
    # Мастер-классы
    {
        "category": "Мастер-классы",
        "name": "Мастер-класс по грувам",
        "price": 2000,
        "duration": 90,
        "description": "Разбор 10 культовых грувов из разных жанров. Сразу играем, минимум теории.",
        "is_active": True,
    },
    {
        "category": "Мастер-классы",
        "name": "Мастер-класс по педальной технике",
        "price": 2000,
        "duration": 90,
        "description": "Двойная педаль, независимость ноги, скорость. Подходит для уровня intermediate+.",
        "is_active": True,
    },
    {
        "category": "Мастер-классы",
        "name": "Мастер-класс по латине",
        "price": 2000,
        "duration": 90,
        "description": "Боса-нова, самба, мамбо — ритмы латиноамериканской музыки на барабанах.",
        "is_active": True,
    },
    # Интенсивы
    {
        "category": "Интенсивы",
        "name": "Интенсив выходного дня",
        "price": 8000,
        "duration": 240,
        "description": "Полный день занятий: техника, репертуар, разбор ошибок. Максимальный результат за одну сессию.",
        "is_active": True,
    },
    {
        "category": "Интенсивы",
        "name": "Подготовка к выступлению",
        "price": 5000,
        "duration": 120,
        "description": "Работаем конкретно под сет или живое выступление. Стабильность, динамика, уверенность на сцене.",
        "is_active": True,
    },
]

# master_name -> list of service names they teach
MASTER_SERVICE_ASSIGNMENTS = {
    "Алексей Громов": [
        "Пробное занятие",
        "Занятие для начинающих",
        "Занятие среднего уровня",
        "Занятие продвинутого уровня",
        "Двойная сессия",
        "Джазовый ансамбль",
        "Мастер-класс по грувам",
        "Интенсив выходного дня",
        "Подготовка к выступлению",
    ],
    "Мария Темникова": [
        "Пробное занятие",
        "Занятие для начинающих",
        "Занятие среднего уровня",
        "Группа для новичков",
        "Мастер-класс по педальной технике",
    ],
    "Дмитрий Кравцов": [
        "Занятие для начинающих",
        "Занятие среднего уровня",
        "Занятие продвинутого уровня",
        "Джазовый ансамбль",
        "Мастер-класс по латине",
        "Подготовка к выступлению",
    ],
}


def seed(db: Session) -> None:
    if db.query(Service).count() > 0:
        return

    services = {}
    for svc_data in SERVICES_DATA:
        svc = Service(**svc_data)
        db.add(svc)
        db.flush()
        services[svc.name] = svc

    masters = {}
    for m_data in MASTERS_DATA:
        master = Master(**m_data)
        db.add(master)
        db.flush()
        masters[master.name] = master

    for master_name, service_names in MASTER_SERVICE_ASSIGNMENTS.items():
        master = masters[master_name]
        for svc_name in service_names:
            svc = services[svc_name]
            ms = MasterService(master_id=master.id, service_id=svc.id)
            db.add(ms)

    db.commit()
