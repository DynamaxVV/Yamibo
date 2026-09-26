from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from yamibo_mcp.services.embedded_chat.skill_proposals import SkillProposals
from yamibo_mcp.web_fastapi.deps import get_settings

router = APIRouter(prefix='/api/settings/skill-reviews', tags=['settings'])

class Review(BaseModel):
    model_config = ConfigDict(extra='forbid')
    approve: bool

@router.get('')
def list_reviews(settings=Depends(get_settings)):
    return {'proposals': SkillProposals(settings).list()}

@router.post('/{proposal_id}/review')
def review(proposal_id: str, body: Review, settings=Depends(get_settings)):
    try:
        return SkillProposals(settings).review(proposal_id, approve=body.approve)
    except KeyError:
        raise HTTPException(404, 'SKILL_PROPOSAL_NOT_FOUND')
    except ValueError as exc:
        raise HTTPException(409, str(exc))
