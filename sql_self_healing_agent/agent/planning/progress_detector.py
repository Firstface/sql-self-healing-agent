from sql_self_healing_agent.agent.models.observation import Observation


class ProgressDetector:
    @staticmethod
    def made_progress(observation: Observation, previous: list[Observation]) -> bool:
        signature = (
            observation.action_type,
            observation.summary,
            tuple(observation.artifact_refs),
            tuple(observation.produced_workspace_keys),
        )
        return all(
            signature
            != (
                item.action_type,
                item.summary,
                tuple(item.artifact_refs),
                tuple(item.produced_workspace_keys),
            )
            for item in previous
        )
