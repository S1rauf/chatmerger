from shared.redis_client import redis

class TemplateService:
    @staticmethod
    async def cache_templates():
        templates = await get_all_templates()
        for template in templates:
            await redis.hset(
                "templates",
                template.name,
                template.text
            )
        logging.info(f"Cached {len(templates)} templates")