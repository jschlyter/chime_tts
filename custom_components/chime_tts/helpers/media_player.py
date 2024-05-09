"""Media player helper functions for Chime TTS."""

import logging
import time
import math
from homeassistant.core import HomeAssistant, State
from homeassistant.const import CONF_ENTITY_ID, SERVICE_TURN_ON, SERVICE_VOLUME_SET

from homeassistant.components.media_player.const import (
    ATTR_MEDIA_VOLUME_LEVEL,
    ATTR_MEDIA_ANNOUNCE,
    ATTR_GROUP_MEMBERS,
    SERVICE_JOIN,
)
from ..const import (
    ALEXA_MEDIA_PLAYER_PLATFORM,
    SPOTIFY_PLATFORM,
    SONOS_PLATFORM,
    MEDIA_DIR_DEFAULT,
    TRANSITION_STEP_MS
)
_LOGGER = logging.getLogger(__name__)

class MediaPlayerHelper:
    """Media player helper functions for Chime TTS."""

    async def async_initialize_media_players(self, hass: HomeAssistant, entity_ids, volume_level):
        """Initialize media player entities."""
        # Service call was from chime_tts.say_url, so media_players are irrelevant
        if len(entity_ids) == 0:
            return []

        media_players_array = []
        for entity_id in entity_ids:
            media_player_dict = await self.async_get_media_player_dict(hass, entity_id, volume_level)
            if media_player_dict:
                media_players_array.append(media_player_dict)

        if len(media_players_array) == 0:
            _LOGGER.error("No valid media players found")

        return media_players_array

    async def async_get_media_player_dict(self, hass: HomeAssistant, entity_id, volume_level):
        """Create a media player dictionary object for a given entity_id."""
        # Validate media player entity_id
        entity = hass.states.get(entity_id)
        media_player_is_spotify = self.get_is_media_player_spotify(hass, entity_id)
        if entity is None:
            return None

        # Ensure media player is turned on
        if entity.state == "off":
            _LOGGER.info(
                'Media player entity "%s" is turned off. Turning on...', entity_id
            )
            await hass.services.async_call(
                domain="media_player",
                service=SERVICE_TURN_ON,
                service_data={CONF_ENTITY_ID: entity_id},
                blocking=True
            )

        group_member_support = self.get_supported_feature(entity, ATTR_GROUP_MEMBERS)
        announce_supported = self.get_supported_feature(entity, ATTR_MEDIA_ANNOUNCE)
        is_playing = (hass.states.get(entity_id).state == "playing"
                        and hass.states.get(entity_id).attributes.get("media_duration", -1) != 0) # Check that media_player is actually playing (HomePods can incorrectly have the state "playing" when no media is playing)

        # Playback volume level
        playback_volume_level = volume_level
        if isinstance(volume_level, dict):
            playback_volume_level = volume_level.get(entity_id, -1.0)

        # Store media player's current volume level
        should_change_volume = False
        should_change_volume = playback_volume_level >= 0 or media_player_is_spotify
        initial_volume_level = float(
            entity.attributes.get(ATTR_MEDIA_VOLUME_LEVEL, -1.0)
        )

        return {
            "entity_id": entity_id,
            "platform": self.get_media_player_platform(hass, entity_id),
            "should_change_volume": should_change_volume,
            "initial_volume_level": initial_volume_level,
            "playback_volume_level": playback_volume_level,
            "group_members_supported": group_member_support,
            "announce_supported": announce_supported,
            "is_playing": is_playing,
        }

    def parse_entity_ids(self, data, hass):
        """Parse media_player entity_ids into list object."""
        entity_ids = data.get(CONF_ENTITY_ID, [])
        if isinstance(entity_ids, str):
            entity_ids = entity_ids.split(",")

        # Find all media_player entities associated with device/s specified
        device_ids = data.get("device_id", [])
        if isinstance(device_ids, str):
            device_ids = device_ids.split(",")
        entity_registry = hass.data["entity_registry"]
        for device_id in device_ids:
            matching_entity_ids = [
                entity.entity_id
                for entity in entity_registry.entities.values()
                if entity.device_id == device_id
                and entity.entity_id.startswith("media_player.")
            ]
            entity_ids.extend(matching_entity_ids)
        entity_ids = list(set(entity_ids))
        return entity_ids

    def get_media_player_platform(self, hass: HomeAssistant, entity_id):
        """Get the platform for a given media_player entity."""
        entity_registry = hass.data["entity_registry"]
        for entity in entity_registry.entities.values():
            if entity.entity_id == entity_id:
                return entity.platform
        return None

    def get_alexa_media_player_count(self, hass: HomeAssistant, entity_ids):
        """Determine whether any included media_players belong to the Alexa Media Player platform."""
        ret_val = 0
        for entity_id in entity_ids:
            if self.get_is_media_player_alexa(hass, entity_id):
                ret_val = ret_val + 1
        return ret_val

    def get_is_standard_media_player(self, hass, entity_id):
        """Determine whether a media_player can be used with the media_player.play_media service."""
        return not (self.get_is_media_player_alexa(hass, entity_id) or
                    self.get_is_media_player_sonos(hass, entity_id) or
                    self.get_is_media_player_spotify(hass, entity_id))

    def get_is_media_player_alexa(self, hass, entity_id):
        """Determine whether a media_player belongs to the Alexa Media Player platform."""
        return str(self.get_media_player_platform(hass, entity_id)).lower() == ALEXA_MEDIA_PLAYER_PLATFORM

    def get_is_media_player_sonos(self, hass, entity_id):
        """Determine whether a media_player belongs to the Sonos platform."""
        return str(self.get_media_player_platform(hass, entity_id)).lower() == SONOS_PLATFORM

    def get_is_media_player_spotify(self, hass, entity_id):
        """Determine whether a media_player belongs to the Spotify platform."""
        return str(self.get_media_player_platform(hass, entity_id)).lower() == SPOTIFY_PLATFORM

    def get_supported_feature(self, entity: State, feature: str):
        """Whether a feature is supported by the media_player device."""
        if entity is None or entity.attributes is None:
            return False
        supported_features = entity.attributes.get("supported_features", 0)

        if feature is ATTR_MEDIA_VOLUME_LEVEL:
            return bool(supported_features & 2)

        if feature is ATTR_MEDIA_ANNOUNCE:
            return bool(supported_features & 1048576)

        if feature is ATTR_GROUP_MEMBERS:
            return bool(supported_features & 524288)

        return False

    def get_join_suppored_entity_ids(self, media_players_array):
        """Get the entity_ids of media player which support the join feature."""
        group_members_suppored_entity_ids = []
        for media_player_dict in media_players_array:
            if media_player_dict.get("group_members_supported", None):
                group_members_suppored_entity_ids.append(media_player_dict["entity_id"])
        return group_members_suppored_entity_ids

    def get_media_content_id(self, file_path: str, media_dir: str = MEDIA_DIR_DEFAULT):
        """Create the media content id for a local media directory file."""
        if file_path is None:
            return None

        media_dir = f"/{media_dir}/".replace("//", "/")
        media_source_path = file_path
        media_folder_path_index = media_source_path.find("/media/")
        if media_folder_path_index != -1:
            media_path = media_source_path[media_folder_path_index + len("/media/") :].replace("//", "/")
            media_source_path = "media-source://media_source<media_dir><media_path>".replace(
                "<media_dir>", f"/{MEDIA_DIR_DEFAULT}/"
            ).replace(
                "<media_path>", media_path)
            return media_source_path

        return None

    async def async_wait_until_media_players_state_is(self, hass: HomeAssistant, media_player_dicts: list, target_state: str, timeout: float = 3.5):
        """Wait until the state of a list of media_players equals a target state."""
        def property(media_player_dict):
            entity_id = media_player_dict["entity_id"]
            return hass.states.get(entity_id).state
        def condition(media_player_dict):
            p_property = property(media_player_dict)
            return p_property == target_state

        _LOGGER.debug(" - Waiting until %s media_player%s %s %s...",
                      len(media_player_dicts),
                      ("" if len(media_player_dicts) == 1 else "s"),
                      ("is" if len(media_player_dicts) == 1 else "are"),
                      target_state)
        return await self._async_wait_until_media_players(hass, media_player_dicts, condition, timeout)

    async def async_wait_until_media_players_state_not(self, hass: HomeAssistant, media_player_dicts: list, target_state: str, timeout: float = 3.5):
        """Wait until the state of a list of media_players no longer equals a target state."""
        def property(media_player_dict):
            entity_id = media_player_dict["entity_id"]
            return hass.states.get(entity_id).state
        def condition(media_player_dict):
            p_property = property(media_player_dict)
            return p_property != target_state

        _LOGGER.debug(" - Waiting until %s media_player%s %s %s...",
                      len(media_player_dicts),
                      ("" if len(media_player_dicts) == 1 else "s"),
                      ("isn't" if len(media_player_dicts) == 1 else "aren't"),
                      target_state)
        return await self._async_wait_until_media_players(hass, media_player_dicts, condition, timeout)

    async def async_wait_until_media_players_volume_level_is(self, hass: HomeAssistant, media_player_dicts: list, target_volume: str, timeout: float = 5):
        """Wait for a media_player to have a target volume_level."""
        def property(media_player_dict):
            entity_id = media_player_dict["entity_id"]
            return hass.states.get(entity_id).attributes.get(ATTR_MEDIA_VOLUME_LEVEL, -1)
        def condition(media_player_dict):
            p_property = property(media_player_dict)
            return p_property == target_volume

        _LOGGER.debug(" - Waiting until %s media_player%s volume_level %s %s...",
                      len(media_player_dicts),
                      ("" if len(media_player_dicts) == 1 else "s"),
                      ("is" if len(media_player_dicts) == 1 else "are"),
                      target_volume)
        return await self._async_wait_until_media_players(hass, media_player_dicts, condition, timeout)

    async def _async_wait_until_media_players(self, hass: HomeAssistant, media_player_dicts: list, condition, timeout: float = 3.5):
        """Wait until the state of a list of media_players equals/no longer equals a target state."""
        # Validation
        if (hass is None or media_player_dicts is None or len(media_player_dicts) == 0 or condition is None):
            return False
        for media_player_dict in media_player_dicts:
            entity_id = media_player_dict["entity_id"]
            if not hass.states.get(str(entity_id)):
                _LOGGER.warning("Invalid entity_id: %s", str(entity_id))
                return False

        delay = 0.2
        still_waiting = list(media_player_dicts)
        while len(still_waiting) > 0 and timeout > 0:
            for media_player_dict in still_waiting:
                if condition(media_player_dict):
                    _LOGGER.debug("   ✔ %s", media_player_dict["entity_id"])
                    index = still_waiting.index(media_player_dict)
                    del still_waiting[index]
            timeout = timeout - delay

            if len(still_waiting) > 0:
                await hass.async_add_executor_job(time.sleep, delay)

        if len(still_waiting) > 0:
            for media_player_dict in still_waiting:
                _LOGGER.debug("   𝘅 %s - Timed out", media_player_dict["entity_id"])

        return len(still_waiting) == 0

    async def async_wait_until_media_player_volume_level(self, hass: HomeAssistant, media_player_dicts: list, target_volume: str, timeout=5):
        """Wait for a media_player to have a target volume_level."""
        delay = 0.2
        volume_reached = False
        while not volume_reached and timeout > 0:
            volume_reached = True
            for media_player_dict in media_player_dicts:
                entity_id = media_player_dict["entity_id"]
                if hass.states.get(entity_id):
                    volume = round(hass.states.get(entity_id).attributes.get(ATTR_MEDIA_VOLUME_LEVEL, -1), 3)
                    if volume != round(target_volume, 3):
                        _LOGGER.debug("%s's current volume: %s. Waiting for volume: %s...", entity_id, str(volume), str(round(target_volume, 3)))
                        volume_reached = False
                    else:
                        _LOGGER.debug(" - %s's volume_level reached target volume: %s", entity_id, str(target_volume))
            if volume_reached is False:
                await hass.async_add_executor_job(time.sleep, delay)
                timeout = timeout - delay
            else:
                return True
        if volume_reached is False:
            for media_player_dict in media_player_dicts:
                entity_id = media_player_dict["entity_id"]
                if hass.states.get(entity_id):
                    volume = round(hass.states.get(entity_id).attributes.get(ATTR_MEDIA_VOLUME_LEVEL, -1), 3)
                    if volume != round(target_volume, 3):
                        _LOGGER.warning("Timed out. %s's current volume is %s, did not reach target volume: %s",
                                        entity_id,
                                        str(volume),
                                        str(target_volume))
            return False

        return True

    async def async_set_volume_for_media_players(self, hass: HomeAssistant, media_player_dicts, volume_key, fade_duration: int):
        """Fade media players to a volume level."""
        if media_player_dicts is None or len(media_player_dicts) == 0:
            return

        fade_duration = fade_duration / 1000 # Convert from miliseconds to seconds
        fade_steps = math.ceil((fade_duration*1000)/TRANSITION_STEP_MS) if fade_duration > 0 else 1
        delay_s = float(fade_duration / fade_steps)

        if fade_steps > 1:
            for step in range(0, fade_steps):
                for media_player_dict in media_player_dicts:
                    entity_id = media_player_dict["entity_id"]
                    target_volume = media_player_dict.get(str(volume_key), volume_key)
                    current_volume = float(hass.states.get(entity_id).attributes.get(ATTR_MEDIA_VOLUME_LEVEL, 0))

                    # Skip media_player if already at target volume
                    if target_volume == current_volume and step == 0:
                        continue

                    # Skip media_player if target volume is -1
                    if target_volume == -1:
                        continue

                    # Determine volume steps on first loop
                    if step == 0:
                        volume_step = (target_volume - current_volume) / fade_steps
                        _LOGGER.debug(" - Fading %s %s's volume from %s to %s over %ss",
                                    ("in" if volume_step > 0 else "out"),
                                    entity_id,
                                    str(current_volume),
                                    str(target_volume),
                                    str(fade_duration))
                        media_player_dict["volume_steps"] = []
                        for i in range(1, fade_steps):
                            volume_step_i = current_volume + (volume_step * i)
                            media_player_dict["volume_steps"].append(volume_step_i)

                    # Step volume or target volume
                    new_volume = round(max(float(media_player_dict["volume_steps"][step] if len(media_player_dict["volume_steps"]) > step else target_volume), 0), 4)
                    try:
                        await hass.services.async_call(
                            domain="media_player",
                            service=SERVICE_VOLUME_SET,
                            service_data={
                                ATTR_MEDIA_VOLUME_LEVEL: new_volume,
                                CONF_ENTITY_ID: entity_id
                            },
                            blocking=True
                        )
                    except Exception as error:
                        _LOGGER.warning("Unable to fade %s's volume to %s: %s", entity_id, str(new_volume), error)
                if step != fade_steps-1:
                    await hass.async_add_executor_job(time.sleep, delay_s)
        else:
            for media_player_dict in media_player_dicts:
                entity_id = media_player_dict["entity_id"]
                current_volume = float(hass.states.get(entity_id).attributes.get(ATTR_MEDIA_VOLUME_LEVEL, 0))
                target_volume = media_player_dict.get(str(volume_key), volume_key)
                if target_volume >= 0 and target_volume != current_volume:
                    if target_volume - current_volume > 0:
                        _LOGGER.debug(" - Increasing %s's volume from %s to %s", entity_id, str(current_volume), str(target_volume))
                    else:
                        _LOGGER.debug(" - Decresing %s's volume from %s to %s", entity_id, str(current_volume), str(target_volume))
                    try:
                        await hass.services.async_call(
                            domain="media_player",
                            service=SERVICE_VOLUME_SET,
                            service_data={
                                ATTR_MEDIA_VOLUME_LEVEL: target_volume,
                                CONF_ENTITY_ID: entity_id
                            },
                            blocking=True,
                        )
                    except Exception as error:
                        _LOGGER.warning("Unable to set %s's volume to %s: %s", entity_id, str(target_volume), error)

    async def async_join_media_players(self, hass: HomeAssistant, entity_ids):
        """Join media players."""
        _LOGGER.debug(
            "   - Calling media_player.join service for %s media_player entities...",
            len(entity_ids),
        )

        supported_entity_ids = []
        for entity_id in entity_ids:
            entity = hass.states.get(entity_id)
            if self.get_supported_feature(entity, ATTR_GROUP_MEMBERS):
                supported_entity_ids.append(entity_id)

        if len(supported_entity_ids) > 1:
            _LOGGER.debug(
                "   - Joining %s media_player entities:", str(len(supported_entity_ids))
            )
            for supported_entity_id in supported_entity_ids:
                _LOGGER.debug("     - %s", supported_entity_id)
            try:
                join_media_player_entity_id = supported_entity_ids[0]
                await hass.services.async_call(
                    domain="media_player",
                    service=SERVICE_JOIN,
                    service_data={
                        CONF_ENTITY_ID: join_media_player_entity_id,
                        ATTR_GROUP_MEMBERS: supported_entity_ids,
                    },
                    blocking=True,
                )
                _LOGGER.debug("     ...done")
                return join_media_player_entity_id
            except Exception as error:
                _LOGGER.warning("Error joining media_player entities: %s", error)
        else:
            _LOGGER.warning("Only 1 media_player entity provided. Unable to join.")

        return False
