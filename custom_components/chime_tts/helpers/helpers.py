"""Audio helper functions for Chime TTS."""

import logging
import os
import re
import subprocess
import shutil
import yaml
import aiofiles
import aiofiles.os
from homeassistant.core import HomeAssistant
from homeassistant.components.media_player.const import (
    ATTR_MEDIA_VOLUME_LEVEL,
)
import voluptuous as vol
from pydub import AudioSegment
from ..const import (
    DOMAIN,
    SERVICE_SAY,
    SERVICE_SAY_URL,
    DEFAULT_CHIME_OPTIONS,
    OFFSET_KEY,
    DEFAULT_OFFSET_MS,
    FFMPEG_ARGS_ALEXA,
    FFMPEG_ARGS_VOLUME,
    AMAZON_POLLY,
    BAIDU,
    ELEVENLABS_TTS,
    GOOGLE_CLOUD,
    GOOGLE_TRANSLATE,
    IBM_WATSON_TTS,
    MARYTTS,
    MICROSOFT_EDGE_TTS,
    MICROSOFT_TTS,
    NABU_CASA_CLOUD_TTS,
    NABU_CASA_CLOUD_TTS_OLD,
    OPENAI_TTS,
    PICOTTS,
    PIPER,
    VOICE_RSS,
    YANDEX_TTS,
    QUOTE_CHAR_SUBSTITUTE
)
from .media_player_helper import MediaPlayerHelper
from .media_player import ChimeTTSMediaPlayer
from .filesystem import FilesystemHelper

filesystem_helper = FilesystemHelper()

_LOGGER = logging.getLogger(__name__)
class ChimeTTSHelper:
    """Helper functions for Chime TTS."""

    # Services.yaml

    async def async_parse_services_yaml(self):
        """Load the services.yaml file into a dictionary."""
        services_file_path = os.path.join(os.path.dirname(__file__), '../services.yaml')

        try:
            async with aiofiles.open(services_file_path, mode='r') as file:
                services_yaml = yaml.safe_load(await file.read())
                return services_yaml
        except FileNotFoundError:
            _LOGGER.error("services.yaml file not found at %s", services_file_path)
            return
        except yaml.YAMLError as e:
            _LOGGER.error("Error parsing services.yaml: %s", str(e))
            return
        except Exception as e:
            _LOGGER.error("Unexpected error reading services.yaml: %s", str(e))
            return

    async def async_save_services_yaml(self, services_yaml):
        """Save a dictionary to the services.yaml file."""

        services_file_path = os.path.join(os.path.dirname(__file__), '../services.yaml')

        try:
            async with aiofiles.open(services_file_path, mode='w') as file:
                await file.write(yaml.safe_dump(services_yaml, default_flow_style=False, sort_keys=False))

            _LOGGER.info("Updated services.yaml with new chime options.")
        except Exception as e:
            _LOGGER.error("Unexpected error updating services.yaml: %s", str(e))

    async def async_update_services_yaml(self, chimes_dir: str):
        """Modify the chime path drop down options."""

        services_yaml = await self.async_parse_services_yaml()
        if not services_yaml:
            return

        try:
            # List of chime options from chimes folder
            custom_chime_options = filesystem_helper.get_chime_options_from_path(chimes_dir)

            # Chime Paths
            final_options: list = DEFAULT_CHIME_OPTIONS + custom_chime_options
            final_options = sorted(final_options, key=lambda x: x['label'].lower())
            if not custom_chime_options:
                final_options.append({"label": "*** Add a local folder path in the configuration for your own custom chimes ***", "value": None})

            # New chimes detected?
            if final_options != services_yaml['say']['fields']['chime_path']['selector']['select']['options']:
                _LOGGER.debug("Adding new chimes")
                # Update `say` and `say_url` chime path fields
                services_yaml['say']['fields']['chime_path']['selector']['select']['options'] = list(final_options)
                services_yaml['say']['fields']['end_chime_path']['selector']['select']['options'] = list(final_options)
                services_yaml['say_url']['fields']['chime_path']['selector']['select']['options'] = list(final_options)
                services_yaml['say_url']['fields']['end_chime_path']['selector']['select']['options'] = list(final_options)

                await self.async_save_services_yaml(services_yaml)
        except Exception as e:
            _LOGGER.error("Unexpected error updating services.yaml: %s", str(e))

    async def async_get_schema_for_service(self, service_name: str):
        """Modify the chime path drop down options."""

        service_yaml = await self.async_parse_services_yaml()
        if not service_yaml or service_name not in service_yaml:
            return
        service_info = service_yaml[service_name]
        fields = service_info.get('fields', {})
        schema = {}

        # Process each field in the service
        for field_name, field_info in fields.items():
            selector = field_info.get('selector', {})

            if selector and selector.get('text'):
                multiline = selector['text'].get('multiline', False)
                if multiline:
                    schema[vol.Required(field_name)] = vol.All(vol.Coerce(str), vol.Length(max=1024))
                else:
                    schema[vol.Required(field_name)] = vol.Coerce(str)
            elif selector and selector.get('select'):
                options = [option['value'] for option in selector['select']['options']]
                schema[vol.Required(field_name)] = vol.In(options)
            elif selector and selector.get('boolean'):
                schema[vol.Required(field_name)] = vol.Coerce(bool)
            elif selector and selector.get('number'):
                number_selector = selector['number']
                schema[vol.Required(field_name)] = vol.All(
                    vol.Coerce(float),
                    vol.Range(
                        min=number_selector.get('min', None),
                        max=number_selector.get('max', None),
                    ),
                )
            else:
                schema[vol.Required(field_name)] = vol.Coerce(str)  # Default to string if selector type is missing

        return schema

    async def async_update_chime_lists(self,
                                       hass,
                                       custom_chimes_folder_path,
                                       say_service_func,
                                       say_url_service_func):
        """Update the list of chimes for the say and say-url services."""
        await self.async_update_services_yaml(custom_chimes_folder_path)
        hass.services.async_remove(DOMAIN, SERVICE_SAY)
        hass.services.async_register(DOMAIN, SERVICE_SAY, say_service_func)
        hass.services.async_remove(DOMAIN, SERVICE_SAY_URL)
        hass.services.async_register(DOMAIN, SERVICE_SAY_URL, say_url_service_func)

    # Parameters / Options

    async def async_parse_params(self, hass: HomeAssistant, data, is_say_url, media_player_helper: MediaPlayerHelper):
        """Parse TTS service parameters."""
        entity_ids = media_player_helper.parse_entity_ids(data, hass) if is_say_url is False else []
        chime_path =str(data.get("chime_path", ""))
        end_chime_path = str(data.get("end_chime_path", ""))
        offset = float(data.get("delay", data.get(OFFSET_KEY, DEFAULT_OFFSET_MS)))
        final_delay = float(data.get("final_delay", 0))
        message = str(data.get("message", ""))
        tts_platform = str(data.get("tts_platform", ""))
        tts_speed = float(data.get("tts_playback_speed", data.get("tts_speed", 100)))
        tts_pitch = data.get("tts_pitch", 0)
        volume_level = data.get(ATTR_MEDIA_VOLUME_LEVEL, -1)
        join_players = data.get("join_players", False)
        unjoin_players = data.get("unjoin_players", False)
        language = data.get("language", None)
        cache = data.get("cache", False)
        announce = data.get("announce", False)
        fade_audio = data.get("fade_audio", False)
        media_players_array = await media_player_helper.async_initialize_media_players(
            hass, entity_ids, volume_level, join_players, unjoin_players, announce, fade_audio
        ) if is_say_url is False else []

        # No valid media players included
        if len(media_players_array) == 0 and is_say_url is False:
            return None

        # FFmpeg arguments
        ffmpeg_args: str = self.parse_ffmpeg_args(data.get("audio_conversion", None))

        # Force "Alexa" conversion if any Alexa media_player entities included
        alexa_conversion_forced = False
        if ffmpeg_args is None and media_player_helper.get_alexa_media_player_count(hass, entity_ids) > 0:
            ffmpeg_args = FFMPEG_ARGS_ALEXA
            alexa_conversion_forced = True

        params = {
            "entity_ids": entity_ids,
            "hass": hass,
            "chime_path": chime_path,
            "end_chime_path": end_chime_path,
            "cache": cache,
            "offset": offset,
            "final_delay": final_delay,
            "message": message,
            "language": language,
            "tts_platform": tts_platform,
            "tts_speed": tts_speed,
            "tts_pitch": tts_pitch,
            "announce": announce,
            "fade_audio": fade_audio,
            "volume_level": volume_level,
            "join_players": join_players,
            "unjoin_players": unjoin_players,
            "ffmpeg_args": ffmpeg_args,
            "media_players_array": media_players_array,
        }

        _LOGGER.debug("----- General Parameters -----")
        for key, value in params.items():
            if value is not None and value != "" and key not in ["hass"]:
                p_key = "audio_conversion" if key == "ffmpeg_args" else key
                if isinstance(value, list) and ((p_key != "audio_conversion"
                                                 and len(value) > 1)
                                                 or (p_key == "media_players_array" and len(value) > 0)):
                    _LOGGER.debug(" * %s:", p_key)
                    for i in range(0, len(value)):
                        if isinstance(value[i], ChimeTTSMediaPlayer):
                            media_player_i = value[i]
                            _LOGGER.debug("   - %s: entity_id: %s", str(i), str(media_player_i.entity_id))
                            _LOGGER.debug("     platform: %s", str(media_player_i.platform))
                            _LOGGER.debug("     initial volume: %s", str(media_player_i.initial_volume_level))
                            _LOGGER.debug("     target volume: %s", str(media_player_i.target_volume_level))
                            _LOGGER.debug("     now playing: %s", str(media_player_i.initially_playing))
                            _LOGGER.debug("     join supported: %s", str(media_player_i.join_supported))
                            _LOGGER.debug("     announce supported: %s", str(media_player_i.announce_supported))
                        else:
                            _LOGGER.debug("   - %s: %s", str(i), str(value[i]))
                else:
                    _LOGGER.debug(" * %s = %s", p_key, str(value))

        if alexa_conversion_forced is True:
            _LOGGER.debug(" --- Audio will be converted to Alexa-friendly format as Alexa speaker/s detected ---")
        return params

    def parse_options_yaml(self, data: dict = {}):
        """Parse TTS service options YAML into dict object."""
        options = {}
        try:
            options_string = data.get("options", "")
            options = self.convert_yaml_str(options_string)
            if options is None:
                options = {}
        except yaml.YAMLError as error:
            _LOGGER.error("Error parsing options YAML: %s", error)
            return {}
        except Exception as error:
            _LOGGER.error("An unexpected error occurred while parsing options YAML: %s",
                          str(error))

        for key in ["tld", "voice"]:
            if key not in options:
                value = data.get(key, None)
                if value is not None:
                    options[key] = value

        if len(options) > 0:
            _LOGGER.debug("----- TTS-Specific Params -----")
            for key, value in options.items():
                _LOGGER.debug(" * %s = %s", key, str(value))

        return options

    def remove_niqqud(self, message_text: str):
        """Replace Hebrew niqqud characters with non-voweled characters."""
        # Unicode range for Hebrew niqqud is \u0591 to \u05C7
        niqqud_pattern = re.compile(r'[\u0591-\u05C7]')
        cleaned_text = niqqud_pattern.sub('', message_text)
        return cleaned_text

    def parse_message(self, message_string: str):
        """Parse the message string/YAML object into segments dictionary."""
        message_string = self.remove_niqqud(message_string)
        segments = []
        if len(message_string) == 0 or message_string == "None":
            return []

        contains_keys = True
        for key in ["type", "tts", "chime", "delay"]:
            contains_keys = contains_keys or message_string.find(f"'{key}':") > -1 or message_string.find(f'"{key}":') > -1
        if contains_keys:
            # Convert message string to YAML object
            message_yaml = self.convert_yaml_str(message_string)

            # Verify objects in YAML are valid chime/tts/delay segements
            if isinstance(message_yaml, list):
                is_valid = True
                for elem in message_yaml:
                    if isinstance(elem, dict):

                        # Convert new short format to old format
                        if "type" not in elem:
                            # Chime
                            if "chime" in elem:
                                elem["type"] = "chime"
                                elem["path"] = elem["chime"].replace(QUOTE_CHAR_SUBSTITUTE, "'")
                                del elem["chime"]
                            # TTS
                            elif "tts" in elem:
                                elem["type"] = "tts"
                                elem["message"] = elem["tts"].replace(QUOTE_CHAR_SUBSTITUTE, "'")
                                del elem["tts"]
                            # Delay
                            elif "delay" in elem:
                                elem["type"] = "delay"
                                elem["length"] = elem["delay"]
                                del elem["delay"]
                            else:
                                is_valid = False
                    else:
                        is_valid = False
                if is_valid is True:
                    segments = message_yaml

        # Add message string as TTS segment
        if len(segments) == 0:
            segments.append({
                'type': 'tts',
                'message': message_string
            })

        # Final adjustments
        final_segments = []
        for _, segment_n in enumerate(segments):
            segment = {}
            for key, value in segment_n.items():
                if isinstance(value, dict):
                    for key_n, value_n in value.items():
                        value[key_n.lower()] = value_n
                # Make all segment keys lowercase
                segment[key.lower()] = value

            # Support alternate key names
            if segment.get("speed"):
                segment["tts_speed"] = segment.get("speed")
                del segment["speed"]
            if segment.get("pitch"):
                segment["tts_pitch"] = segment.get("pitch")
                del segment["pitch"]

            # Duplicate segments "repeat" times
            repeat = segment.get("repeat", 1)
            if isinstance(repeat, int):
                repeat = max(segment.get("repeat", 1), 1)
            else:
                repeat = 1
            for _ in range(repeat):
                final_segments.append(segment)

        return final_segments

    def convert_yaml_str(self, yaml_string):
        """Convert a yaml string into an object."""
        if not yaml_string:
            return {}
        if isinstance(yaml_string, dict):
            return yaml_string

        try:
            yaml_string = yaml_string.replace("'", "\\'").replace("\\\\'", QUOTE_CHAR_SUBSTITUTE).replace("\\'", "'")
            yaml_object = yaml.safe_load(yaml_string)
            return yaml_object
        except yaml.YAMLError as exc:
            if hasattr(exc, 'problem_mark'):
                _LOGGER.error("Message YAML parsing error at line %s, column %s: %s",
                                str(exc.problem_mark.line + 1),
                                str(exc.problem_mark.column + 1),
                                str(exc))
            else:
                _LOGGER.error("Message YAML error: %s", str(exc))
        except Exception as error:
            _LOGGER.error("An unexpected error occurred while parsing message YAML: %s",
                            str(error))


    def parse_ffmpeg_args(self, ffmpeg_args_str: str):
        """Parse the FFmpeg argument string."""
        if ffmpeg_args_str is not None:
            if ffmpeg_args_str.lower() == "alexa":
                return FFMPEG_ARGS_ALEXA
            if (len(ffmpeg_args_str.split(" ")) == 2 and
                  ffmpeg_args_str.split(" ")[0].lower() == "volume"):
                try:
                    volume = float(ffmpeg_args_str.split(" ")[1].replace("%","")) / 100
                    return FFMPEG_ARGS_VOLUME.replace("X", str(volume))
                except ValueError:
                    _LOGGER.warning("Error parsing audio_conversion string")
            if ffmpeg_args_str.lower() == "custom":
                return None
        return ffmpeg_args_str

    def get_tts_platform(self,
                         hass,
                         tts_platform: str = "",
                         default_tts_platform: str = ""):
        """TTS platform/entity_id to use for TTS audio."""

        installed_tts_platforms = self.get_installed_tts_platforms(hass)

        # Match for deprecated Nabu Casa platform string
        if tts_platform.lower() == NABU_CASA_CLOUD_TTS_OLD:
            return NABU_CASA_CLOUD_TTS

        # Match for installed tts platform
        for tts_provider_n in installed_tts_platforms:
            if tts_platform == tts_provider_n:
                return tts_provider_n

        # Contains "google" - return alternate Google platform, if available
        if tts_platform.find("google") != -1:
            # Return alternate Google Translate entity, eg: "tts.google_en_com"
            if tts_platform.startswith("tts."):
                for tts_provider_n in installed_tts_platforms:
                    if (tts_provider_n.lower().find("google") != -1
                        and tts_provider_n.startswith("tts.")):
                        _LOGGER.warning("The TTS entity '%s' was not found. Using '%s' instead.", tts_platform, tts_provider_n)
                        return tts_provider_n
            # Return Google Translate, if installed
            for tts_provider_n in installed_tts_platforms:
                if tts_provider_n.lower() == GOOGLE_TRANSLATE:
                    _LOGGER.warning("The TTS platform '%s' was not found. Using '%s' instead.", tts_platform, GOOGLE_TRANSLATE)
                    return GOOGLE_TRANSLATE

        # Return default
        return self.get_default_tts_platform(hass, default_tts_platform)


    def get_stripped_tts_platform(self, hass, tts_provider):
        """Validate the TTS platform name."""
        stripped_tts_provider = tts_provider.replace(" ", "").replace(" ", "").replace(" ", "").replace(".", "").replace("-", "").replace("_", "").lower()
        if stripped_tts_provider == "amazonpolly":
            tts_provider = AMAZON_POLLY
        elif stripped_tts_provider == "baidu":
            tts_provider = BAIDU
        elif stripped_tts_provider == "elevenlabstts":
            tts_provider = ELEVENLABS_TTS
        elif stripped_tts_provider == "googlecloud":
            tts_provider = GOOGLE_CLOUD
        elif stripped_tts_provider == "googletranslate":
            tts_provider = GOOGLE_TRANSLATE
        elif stripped_tts_provider == "watsontts":
            tts_provider = IBM_WATSON_TTS
        elif stripped_tts_provider == "marytts":
            tts_provider = MARYTTS
        elif stripped_tts_provider == "microsofttts":
            tts_provider = MICROSOFT_TTS
        elif stripped_tts_provider == "microsoftedgetts":
            tts_provider = MICROSOFT_EDGE_TTS
        elif stripped_tts_provider in ["nabucasacloudtts",
                                    "nabucasacloud",
                                    "nabucasa",
                                    "cloudsay"]:
            tts_provider = NABU_CASA_CLOUD_TTS
        elif stripped_tts_provider == "openaitts":
            tts_provider = OPENAI_TTS
        elif stripped_tts_provider == "picotts":
            tts_provider = PICOTTS
        elif stripped_tts_provider == "piper":
            tts_provider = PIPER
        elif stripped_tts_provider == "voicerss":
            tts_provider = VOICE_RSS
        elif stripped_tts_provider == "yandextts":
            tts_provider = YANDEX_TTS

        return tts_provider

    def get_installed_tts_platforms(self, hass: HomeAssistant) -> list[str]:
        """List of installed tts platforms."""
        # Installed TTS Providers
        tts_providers = list((hass.data["tts_manager"].providers).keys())

        # Installed TTS Platform Entities
        tts_entities = []
        all_entities = hass.states.async_all()
        for entity in all_entities:
            if str(entity.entity_id).startswith("tts."):
                tts_entities.append(str(entity.entity_id))

        # TTS Platforms
        installed_tts_platforms: list[str] = tts_providers + tts_entities

        return installed_tts_platforms


    def get_default_tts_platform(self, hass: HomeAssistant, default_tts_platform: str = ""):
        """User's default TTS platform name."""
        installed_tts = list((hass.data["tts_manager"].providers).keys())

        # No TTS platforms available
        if len(installed_tts) == 0:
            _LOGGER.error("Chime TTS could not find any TTS platforms installed. Please add at least 1 TTS integration: https://www.home-assistant.io/integrations/#text-to-speech")
            return False

        # No default configured
        if default_tts_platform is None or len(default_tts_platform) == 0:
            tts_platform = installed_tts[0]
            _LOGGER.debug(" - No `tts_platform` parameter was provided and no default TTS platform/entity has been set in the configuration configuration. Using '%s'", tts_platform)
            return tts_platform

        # Default TTS platform / TTS entity found
        if (default_tts_platform in installed_tts
            or hass.states.get(default_tts_platform)):
            _LOGGER.debug(" - Using default TTS platform: '%s'", default_tts_platform)
            return default_tts_platform
        else:
            tts_platform = installed_tts[0]
            _LOGGER.warning(
                "The TTS %s '%s' was not found. Using '%s' instead",
                "entity" if default_tts_platform.startswith("tts.") else "platform",
                default_tts_platform,
                tts_platform)
            return tts_platform

    async def async_ffmpeg_convert_from_audio_segment(self,
                                          audio_segment: AudioSegment = None,
                                          ffmpeg_args: str = "",
                                          folder: str = ""):
        """Convert pydub AudioSegment with FFmpeg and provided arguments."""
        ret_val = audio_segment
        if not audio_segment or not ffmpeg_args or ffmpeg_args == "" or not folder or folder == "":
            return ret_val

        # Save to temp file
        temp_filename = "temp_segment.mp3"
        temp_audio_file = await filesystem_helper.async_save_audio_to_folder(
            audio=audio_segment,
            folder=folder,
            file_name=temp_filename)
        if not temp_audio_file:
            full_path = f"{folder}/{temp_filename}"
            _LOGGER.warning("ffmpeg_convert_from_audio_segment - Unable to store audio segment to: %s", full_path)
            return ret_val

        # Convert with FFmpeg
        converted_audio_file = self.ffmpeg_convert_from_file(temp_audio_file, ffmpeg_args)
        if converted_audio_file is None or converted_audio_file is False or len(converted_audio_file) < 5:
            _LOGGER.warning("ffmpeg_convert_from_audio_segment - Unable to convert audio segment from file %s", temp_audio_file)

        # Load new AudioSegment from converted file
        else:
            try:
                ret_val = await filesystem_helper.async_load_audio(str(converted_audio_file))
            except Exception as error:
                _LOGGER.warning("ffmpeg_convert_from_audio_segment - Unable to load converted audio segment from file: %s. Error: %s",
                                str(converted_audio_file), error)

        # Delete temp file & converted file
        for file_path in [temp_audio_file, converted_audio_file]:
            if (file_path
                and isinstance(file_path, str)
                and os.path.exists(file_path)):
                try:
                    os.remove(file_path)
                except Exception as error:
                    _LOGGER.warning("ffmpeg_convert_from_audio_segment - Unable to delete file: %s. Error: %s",
                                    str(file_path), error)

        return ret_val

    def ffmpeg_convert_from_file(self, file_path: str, ffmpeg_args: str):
        """Convert audio file with FFmpeg and provided arguments."""
        if not os.path.exists(file_path):
            _LOGGER.warning("Unable to perform FFmpeg conversion: source file not found on file system: %s", file_path)
            return False

        try:
            # Add standard arguments
            ffmpeg_cmd = [
                'ffmpeg',
                '-i',
                file_path,
                *ffmpeg_args.split()
            ]

            # Save to a specific file type (eg: -f wav)
            try:
                # Use specified file type
                index = ffmpeg_cmd.index('-f')
                if index >= 0 and len(ffmpeg_args) >= index:
                    file_extension = ffmpeg_cmd[index+1]
                    if file_extension != "mp3":
                        converted_file_path = file_path.replace(".mp3", f".{file_extension}")
            except Exception:
                # Use mp3 as default
                converted_file_path = file_path.replace(".mp3", "_converted.mp3")

            if converted_file_path == file_path:
                converted_file_path = file_path.replace(".mp3", "_converted.mp3")

            # Delete converted output file if it exists
            if os.path.exists(converted_file_path):
                os.remove(converted_file_path)

            ffmpeg_cmd.append(converted_file_path)

            # Convert the audio file
            ffmpeg_cmd_string = " ".join(ffmpeg_cmd)
            _LOGGER.debug("Converting audio: \"%s\"", ffmpeg_cmd_string)
            ffmpeg_process = subprocess.Popen(ffmpeg_cmd,
                                              stdin=subprocess.PIPE,
                                              stdout=subprocess.PIPE,
                                              stderr=subprocess.PIPE)
            _, error_output = ffmpeg_process.communicate()


            if ffmpeg_process.returncode != 0:
                error_message = error_output.decode('utf-8')
                _LOGGER.error(("FFmpeg conversion failed.\n\nArguments string: \"%s\"\n\nError code: %s\n\nError output:\n%s"),
                               str(ffmpeg_process.returncode),
                               str(error_message),
                               ffmpeg_cmd_string)
                return False

            # Replace original with converted file
            if converted_file_path == file_path.replace(".mp3", "_converted.mp3"):
                try:
                    shutil.move(converted_file_path, file_path)
                    return file_path
                except Exception as error:
                    _LOGGER.error("Error renaming file %s to %s. Error: %s. FFmpeg options: %s",
                                   file_path, converted_file_path, error, ffmpeg_cmd_string)
                    return False

            return converted_file_path

        except subprocess.CalledProcessError as error:
            _LOGGER.error("FFmpeg subproces error: %s FFmpeg options: %s",
                           error, ffmpeg_cmd_string)

        except Exception as error:
            _LOGGER.error("FFmpeg unexpected error: %s FFmpeg options: %s",
                           error, ffmpeg_cmd_string)

        return file_path

    def add_atempo_values_to_ffmpeg_args_string(self, tempo: float, ffmpeg_args_string: str = None):
        """Add atempo values (supporting values less than 0.5) to an FFmpeg argument string."""
        tempos = []
        if tempo < 0.5:
            tempos = [0.5]
            remaining = tempo
            while remaining < 0.5:
                remaining /= 0.5
                if remaining >= 0.5:
                    tempos.append(remaining)
                    break
                tempos.append(0.5)
        else:
            tempos = [tempo]

        for tempo_n in tempos:
            if ffmpeg_args_string is None:
                ffmpeg_args_string = f"-af atempo={tempo_n}"
            else:
                ffmpeg_args_string += f",atempo={tempo_n}"

        return ffmpeg_args_string

    async def async_change_speed_of_audiosegment(self, audio_segment: AudioSegment, speed: float = 100.0, temp_folder: str = None):
        """Change the playback speed of an audio segment."""
        if not audio_segment or speed == 100 or speed < 1 or speed > 500:
            if not audio_segment:
                _LOGGER.warning("Cannot change TTS audio playback speed. No audio available")
            elif speed != 100:
                _LOGGER.warning("TTS audio playback speed values must be between 1% and 500%")
            return audio_segment

        _LOGGER.debug(f" -  ...changing TTS playback speed to {str(speed)}% of original")

        tempo = float(speed / 100)

        ffmpeg_args_string = self.add_atempo_values_to_ffmpeg_args_string(tempo)

        return await self.async_ffmpeg_convert_from_audio_segment(
            audio_segment=audio_segment,
            ffmpeg_args=ffmpeg_args_string,
            folder=temp_folder)

    async def async_change_pitch_of_audiosegment(self, audio_segment: AudioSegment, pitch: int = 0, temp_folder: str = None):
        """Change the pitch of an audio segment."""
        if not audio_segment:
            _LOGGER.warning("Cannot change TTS audio pitch. No audio available")
            return audio_segment
        elif pitch == 0.0:
            return audio_segment

        _LOGGER.debug(
            " -  ...changing pitch of TTS audio by %s semitone%s",
            str(pitch),
            ("" if pitch == 1 else "s")
        )

        # Generate FFmpeg arguments string
        pitch_shift = 2 ** (pitch / 12)
        tempo_adjustment = 1 / pitch_shift
        frame_rate = audio_segment.frame_rate
        ffmpeg_args_string = f"-af asetrate={frame_rate}*{pitch_shift}"
        ffmpeg_args_string = self.add_atempo_values_to_ffmpeg_args_string(tempo_adjustment, ffmpeg_args_string)
        return await self.async_ffmpeg_convert_from_audio_segment(
            audio_segment=audio_segment,
            ffmpeg_args=ffmpeg_args_string,
            folder=temp_folder)

    def combine_audio(self,
                      audio_1: AudioSegment,
                      audio_2: AudioSegment,
                      offset: int = 0):
        """Combine two AudioSegment object with either a delay (if >0) or overlay (if <0)."""
        if audio_1 is None:
            return audio_2
        if audio_2 is None:
            return audio_1
        ret_val = audio_1 + audio_2

        # Overlay / delay
        if offset < 0:
            _LOGGER.debug("Performing overlay of %sms", str(offset))
            ret_val = self.overlay(audio_1, audio_2, offset)
        elif offset > 0:
            ret_val = audio_1 + (AudioSegment.silent(duration=offset) + audio_2)
        else:
            _LOGGER.debug("Combining audio files with no delay or overlay")

        return ret_val

    def overlay(self, audio_1: AudioSegment, audio_2: AudioSegment, overlay: int = 0):
        """Overlay two audio segments."""
        overlay = abs(overlay)
        overlap_point = len(audio_1) - overlay
        overlap_point = max(0, overlap_point)

        crossover_audio = audio_1.overlay(audio_2, position=overlap_point)
        if len(audio_2) > overlay:
            crossover_audio += audio_2[overlay:]
        return crossover_audio
