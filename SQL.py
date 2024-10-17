from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Column,Integer,String,DateTime,Boolean
from datetime import datetime
Base = declarative_base()
class MPDatabase(Base):
    __tablename__ = 'mp_database'
    id = Column(Integer, primary_key=True)
    img_name = Column(String)
    bubble_num = Column(Integer)
    trace_num = Column(Integer)
    update_time = Column(DateTime,default=datetime.now)

