from SonoffPowElite import SonoffPowElite
from WallboxController import WallboxController

solar = SonoffPowElite("WallboxSwSolar")
mains = SonoffPowElite("WallboxSwMains")
wallbox = WallboxController(solar, mains)
wallbox.start()

wallbox.set_mode("solar")  # Solar ON, Mains OFF
print(wallbox.get_status())
wallbox.set_mode("mains")  # Solar OFF, Mains ON
wallbox.set_mode("off")    # beide OFF
wallbox.close()
