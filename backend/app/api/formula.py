"""Formula API - 验证和转译"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from app.core.formula.lexer import Lexer
from app.core.formula.parser import parse_formula
from app.core.formula.transpiler import transpile_formula

router = APIRouter()

class FormulaValidateRequest(BaseModel):
    code: str

class FormulaParseRequest(BaseModel):
    code: str

class FormulaTranspileRequest(BaseModel):
    code: str
    symbols: Optional[List[str]] = None

@router.post("/validate")
async def validate_formula(req: FormulaValidateRequest):
    """验证 Formula 代码语法"""
    try:
        lexer = Lexer()
        tokens = lexer.tokenize(req.code)
        
        # 尝试解析
        ast = parse_formula(req.code)
        
        return {
            "valid": True,
            "name": ast.name,
            "message": "语法正确"
        }
    except SyntaxError as e:
        return {
            "valid": False,
            "error": str(e),
            "message": f"语法错误: {e}"
        }
    except Exception as e:
        return {
            "valid": False,
            "error": str(e),
            "message": "解析失败"
        }

@router.post("/parse")
async def parse_formula_request(req: FormulaParseRequest):
    """解析 Formula 代码，获取参数列表"""
    try:
        ast = parse_formula(req.code)
        
        # 提取参数
        params = []
        for name, param in ast.params.items():
            params.append({
                "name": name,
                "default": param.default,
                "min": param.min_val,
                "max": param.max_val
            })
        
        return {
            "success": True,
            "name": ast.name,
            "params": params
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/transpile")
async def transpile_formula_request(req: FormulaTranspileRequest):
    """转译 Formula 代码为 IR"""
    try:
        ir = transpile_formula(req.code)
        
        # 如果指定了symbols，覆盖默认
        if req.symbols:
            ir["symbols"] = req.symbols
        
        return {
            "success": True,
            "ir": ir
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
